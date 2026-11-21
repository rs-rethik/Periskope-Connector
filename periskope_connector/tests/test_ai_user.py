from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import ai_user, messages
from periskope_connector.patches import guest_records_to_ai_user
from periskope_connector.system import insert_as_system
from periskope_connector.tests.utils import without_site_teams


class TestAiUser(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("ai_user_test")
		self.addCleanup(frappe.db.rollback, save_point="ai_user_test")
		self.addCleanup(frappe.set_user, "Administrator")
		without_site_teams(self, "ai_user_test")
		# a background job has no web request (another test may have left a fake one behind)
		previous = getattr(frappe.local, "request", None)
		frappe.local.request = None
		self.addCleanup(setattr, frappe.local, "request", previous)
		ai_user.ensure()

	def test_the_user_exists_once_without_a_password_or_permissions(self):
		ai_user.ensure()  # a second run changes nothing
		user = frappe.get_doc("User", ai_user.AI_USER)
		self.assertEqual(user.first_name, "Pranera Connect AI")
		self.assertIn(ai_user.AI_ROLE, [r.role for r in user.roles])
		auth = frappe.qb.Table("__Auth")
		password = (
			frappe.qb.from_(auth)
			.select(auth.name)
			.where((auth.doctype == "User") & (auth.name == ai_user.AI_USER) & (auth.fieldname == "password"))
		).run()
		self.assertFalse(password)
		self.assertFalse(frappe.get_all("DocPerm", filters={"role": ai_user.AI_ROLE}))

	def test_a_guest_job_continues_as_the_ai_user(self):
		frappe.set_user("Guest")
		ai_user.act_as_ai()
		self.assertEqual(frappe.session.user, ai_user.AI_USER)

	def test_never_in_a_web_request_or_for_a_signed_in_user(self):
		frappe.set_user("Guest")
		frappe.local.request = MagicMock()
		ai_user.act_as_ai()
		self.assertEqual(frappe.session.user, "Guest")  # the guest session must not change
		frappe.local.request = None
		frappe.set_user("Administrator")
		ai_user.act_as_ai()
		self.assertEqual(frappe.session.user, "Administrator")

	def test_webhook_messages_are_saved_as_the_ai_user(self):
		payload = {
			"event_type": "message.created",
			"data": {
				"message_id": "ai_user_1",
				"chat_id": "919600000019@c.us",
				"from_me": False,
				"message_type": "chat",
				"body": "Price for 200 kg?",
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
		with patch("periskope_connector.ai.classifier.queue_classification", create=True):
			messages.process_webhook_log(log.name)  # the job the webhook queues
		row = frappe.db.get_value(
			"Periskope Message", {"message_id": "ai_user_1"}, ["owner", "periskope_chat"], as_dict=True
		)
		self.assertEqual(row.owner, ai_user.AI_USER)
		self.assertEqual(frappe.db.get_value("Periskope Chat", row.periskope_chat, "owner"), ai_user.AI_USER)

	def test_the_patch_moves_only_the_apps_guest_records(self):
		ours = insert_as_system(
			{"doctype": "Periskope Webhook Log", "event_type": "message.created", "signature_valid": 1}
		)
		other = frappe.get_doc({"doctype": "ToDo", "description": "not ours"}).insert(ignore_permissions=True)
		comment = frappe.get_doc(
			{
				"doctype": "Comment",
				"comment_type": "Info",
				"reference_doctype": "Periskope Webhook Log",
				"reference_name": ours.name,
				"content": "x",
			}
		).insert(ignore_permissions=True)
		for doctype, name in (
			("Periskope Webhook Log", ours.name),
			("ToDo", other.name),
			("Comment", comment.name),
		):
			frappe.db.set_value(doctype, name, "owner", "Guest", update_modified=False)
		frappe.db.set_value("Comment", comment.name, "comment_email", "Guest", update_modified=False)

		guest_records_to_ai_user.execute()
		guest_records_to_ai_user.execute()  # runs again without harm

		self.assertEqual(frappe.db.get_value("Periskope Webhook Log", ours.name, "owner"), ai_user.AI_USER)
		self.assertEqual(
			frappe.db.get_value("Comment", comment.name, ["owner", "comment_email"]),
			(ai_user.AI_USER, ai_user.AI_USER),
		)
		self.assertEqual(frappe.db.get_value("ToDo", other.name, "owner"), "Guest")  # not the app's
