import inspect
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import leads, messages, reminders
from periskope_connector.ai import classifier
from periskope_connector.system import insert_as_system, save_as_system

MACHINE_WRITTEN = [
	"Periskope Message",
	"Periskope Webhook Log",
	"Periskope Reply Reminder",
	"Periskope AI Usage",
	"Periskope Keyword Alert",
]
LOG_ROW = {"doctype": "Periskope Webhook Log", "event_type": "message.created", "signature_valid": 1}
SYSTEM_MANAGER = "test@example.com"


class TestTranslatableMessages(FrappeTestCase):
	"""User-facing text must go through _() so it can be translated."""

	def test_the_webhook_rejection_message_is_translatable(self):
		from periskope_connector.api import webhook

		frappe.local.request = MagicMock()
		frappe.local.request.get_data.return_value = b"{}"
		frappe.local.request_ip = "203.0.113.8"
		with (
			patch.object(webhook, "_", lambda text: f"[translated] {text}"),
			patch.object(webhook, "_has_valid_signature", lambda raw: False),
		):
			with self.assertRaises(frappe.AuthenticationError) as raised:
				webhook.receive()
		self.assertIn("[translated] Invalid signature", str(raised.exception))

	def test_ai_error_messages_are_translatable(self):
		import anthropic

		bad_key = anthropic.AuthenticationError("nope", response=MagicMock(status_code=401), body=None)
		with patch.object(classifier, "_", lambda text: f"[translated] {text}"):
			self.assertEqual(
				classifier.describe_error(bad_key), "[translated] The AI provider rejected the API key"
			)
			server = anthropic.InternalServerError("oops", response=MagicMock(status_code=500), body=None)
			self.assertEqual(
				classifier.describe_error(server), "[translated] The AI provider returned HTTP 500: oops"
			)


class TestSystemWrites(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, always go back to Administrator."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_system_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_system_test")
		self.addCleanup(frappe.set_user, "Administrator")

	def _need_system_manager(self):
		if "System Manager" not in frappe.get_roles(SYSTEM_MANAGER):
			self.skipTest(f"{SYSTEM_MANAGER} is not a System Manager on this site")

	def test_a_guest_cannot_write_a_machine_document_without_the_helper(self):
		# This is why the helper exists: the webhook and the jobs it queues run as Guest.
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			frappe.get_doc(dict(LOG_ROW)).insert()
		self.assertTrue(insert_as_system(dict(LOG_ROW)).name)

	def test_the_bypass_does_not_stay_on_the_returned_document(self):
		log = insert_as_system(dict(LOG_ROW))
		self.assertFalse(log.flags.ignore_permissions)
		frappe.set_user(SYSTEM_MANAGER)
		log.event_type = "changed"
		with self.assertRaises(frappe.PermissionError):
			log.save()  # a System Manager has no write permission on it, and the object keeps no bypass

	def test_save_as_system_updates_a_document_the_user_cannot_edit(self):
		log = insert_as_system(dict(LOG_ROW))
		frappe.set_user(SYSTEM_MANAGER)
		log.event_type = "changed"
		save_as_system(log)
		self.assertEqual(frappe.db.get_value("Periskope Webhook Log", log.name, "event_type"), "changed")
		self.assertFalse(log.flags.ignore_permissions)

	def test_no_role_can_write_the_machine_written_doctypes(self):
		# If someone adds write access here, the system-write helpers need another look.
		for doctype in MACHINE_WRITTEN:
			for perm in frappe.get_meta(doctype).permissions:
				self.assertFalse(perm.write or perm.create, f"{perm.role} can write {doctype}")

	def test_internal_writers_cannot_be_called_over_http(self):
		from periskope_connector import contacts, keywords

		internal = [
			messages.create_message,
			messages.process_webhook_log,
			reminders.create_reminder,
			reminders._check_chat,
			classifier.classify,
			classifier.apply_result,
			classifier._classify_and_commit,
			insert_as_system,
			save_as_system,
			leads.create_lead,
			contacts.ensure_contact,
			contacts.backfill_and_comment,
			keywords.check_message,
		]
		for fn in internal:
			self.assertNotIn(fn, frappe.whitelisted, f"{fn.__name__} must not be whitelisted")

	def test_every_whitelisted_method_checks_who_is_calling(self):
		ours = [
			f for f in frappe.whitelisted if getattr(f, "__module__", "").startswith("periskope_connector")
		]
		self.assertGreaterEqual(len(ours), 8)
		for fn in ours:
			source = inspect.getsource(fn)
			if fn.__name__ == "receive":
				self.assertIn("_has_valid_signature", source)  # guests allowed, signature required
			elif "decide_by_token" in source:
				pass  # the emailed link: the single-use token is the credential
			else:
				# every other endpoint gates the caller (api/permission.py)
				self.assertTrue(
					any(
						check in source
						for check in ("require_admin(", "require_manager(", "require_record_access(")
					),
					fn.__name__,
				)

	def test_the_whole_guest_ingest_chain_works_without_any_user(self):
		"""Webhook log -> message -> AI tags: a queued job runs as the user who queued it, which
		for the webhook is Guest, so none of these steps may need a permission."""
		payload = {
			"event_type": "message.created",
			"data": {
				"message_id": "guest_chain_1",
				"chat_id": "919600000009@c.us",
				"from_me": False,
				"message_type": "chat",
				"body": "Please send the price list for 100 pieces",
				"timestamp": "2031-01-01T00:00:00Z",
			},
		}
		log = insert_as_system(
			{
				"doctype": "Periskope Webhook Log",
				"event_type": "message.created",
				"signature_valid": 1,
				"payload": frappe.as_json(payload),
			}
		)
		frappe.set_user("Guest")

		messages.process_webhook_log(log.name)  # the job queued by the webhook
		name = frappe.db.get_value("Periskope Message", {"message_id": "guest_chain_1"})
		self.assertTrue(name)

		result = {"category": "order_or_enquiry", "severity": "Low", "sentiment": "Neutral"}
		with patch.object(classifier, "call_claude", lambda *a, **k: result):
			classifier.classify(name)  # the job queued after that, also as Guest
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "classification_status"), "Done")

	def test_the_reminder_todo_is_created_under_normal_permissions(self):
		self._need_system_manager()
		name = messages.create_message(
			{
				"event_type": "message.created",
				"data": {
					"message_id": "perm_1",
					"chat_id": "919600000001@c.us",
					"from_me": False,
					"message_type": "chat",
					"body": "Please send the price list",
					"timestamp": "2031-01-01T00:00:00Z",
				},
			}
		)
		row = frappe.get_doc("Periskope Message", name)
		row.message_timestamp = datetime(2031, 1, 1, 9)

		frappe.set_user(SYSTEM_MANAGER)  # what "Check Reply Reminders Now" runs as
		with patch.object(frappe, "sendmail"):
			reminder = reminders.create_reminder(
				"919600000001@c.us",
				[row],
				SYSTEM_MANAGER,
				reminders.get_config(),
				datetime(2031, 1, 1, 10),
				1.0,
			)
			# the ToDo is an SLA row: carry it out, as the engine does
			todo_row = SimpleNamespace(action=reminders.TODO_FOR_REP, recipients="")
			reminders.perform(todo_row, reminder, row, reminders.get_config(), datetime(2031, 1, 1, 12), 3.0)
			reminder.db_set("status", "Escalated")
		self.assertEqual(frappe.db.get_value("ToDo", reminder.todo, "allocated_to"), SYSTEM_MANAGER)
		self.assertEqual(reminder.status, "Escalated")

	def test_the_whatsapp_lead_source_is_created_even_for_a_guest_job(self):
		frappe.db.delete("CRM Lead Source", {"name": "WhatsApp"})
		frappe.set_user("Guest")
		self.assertEqual(leads.ensure_lead_source(), "WhatsApp")
		self.assertTrue(frappe.db.exists("CRM Lead Source", "WhatsApp"))


class TestUninstallCleanup(FrappeTestCase):
	def test_the_phone_fields_are_removed_with_frappes_own_helper_in_one_call(self):
		from periskope_connector import install

		# Patching the real name also proves the helper exists in this Frappe version.
		with patch("frappe.custom.doctype.custom_field.custom_field.delete_custom_fields") as delete:
			install.before_uninstall()
		delete.assert_called_once_with(install.CUSTOM_FIELDS)

	def test_a_failure_is_logged_and_does_not_stop_the_uninstall(self):
		from periskope_connector import install

		with (
			patch(
				"frappe.custom.doctype.custom_field.custom_field.delete_custom_fields",
				side_effect=RuntimeError("boom"),
			),
			patch.object(frappe, "log_error") as log_error,
		):
			install.before_uninstall()
		log_error.assert_called_once()
