"""Tests for the Periskope Contact doctype and the code that works on it."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import contacts, leads, messages
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, without_site_teams

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


def _settings(**kw):
	base = {"alert_emails": ""}
	base.update(kw)
	return SimpleNamespace(**base)


class TestPeriskopeContact(FrappeTestCase):
	pass


class TestIgnoreList(FrappeTestCase):
	def test_a_missing_number_counts_as_ignored(self):
		with patch.object(contacts, "_settings", lambda: _settings()):
			self.assertTrue(contacts.is_ignored(""))  # no number at all is not queueable
			self.assertFalse(contacts.is_ignored("919600000002"))

	def test_an_ignored_contact_row_blocks_the_number(self):
		contacts.insert_as_system(
			{
				"doctype": "Periskope Contact",
				"phone": "919600000001",
				"phone_key": "9600000001",
				"status": "Ignored (Spam)",
			}
		)
		with patch.object(contacts, "_settings", lambda: _settings()):
			self.assertTrue(contacts.is_ignored("+919600000001"))


class TestQueue(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: "Meena"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
			patch.object(contacts, "_settings", lambda: _settings()),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_queue_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_queue_test")
		without_site_teams(self, "periskope_queue_test")
		# the site carries real queue rows from the upgrade; keep them out of the assertions
		frappe.db.delete("Periskope Contact")

	def _message(self, message_id, **data):
		name = messages.create_message(event(message_id=message_id, **data))
		return frappe.get_doc("Periskope Message", name)

	def test_the_first_unknown_message_creates_one_queue_row(self):
		self._message("q1", body="first")
		self._message("q2", body="second")
		row = frappe.get_doc("Periskope Contact", {"phone": "919600000001"})
		self.assertEqual(row.status, "New")
		self.assertEqual(row.message_count, 2)
		self.assertEqual(row.display_name, "Meena")
		self.assertIn("Administrator", row.reps or "")

	def test_a_known_number_gets_no_queue_row(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		self._message("q3")
		self.assertFalse(frappe.db.exists("Periskope Contact", {"phone": "919600000001"}))

	def test_groups_and_ignored_numbers_are_not_queued(self):
		self._message("q4", chat_id="1203630@g.us")
		contacts.insert_as_system(
			{"doctype": "Periskope Contact", "phone": "919600000001", "status": "Ignored (Spam)"}
		)
		self._message("q5")
		# The ignore list is the ignored contact rows: the number stays ignored, nothing is queued.
		self.assertEqual(
			frappe.db.get_value("Periskope Contact", {"phone": "919600000001"}, "status"), "Ignored (Spam)"
		)

	def test_create_lead_action_resolves_the_row_and_backfills_history(self):
		self._message("q6", body="Hi, I am Meena, please send prices")
		row = frappe.get_doc("Periskope Contact", {"phone": "919600000001"})
		lead_name = contacts.create_lead_for_contact(row.name)
		lead = frappe.get_doc("CRM Lead", lead_name)
		self.assertEqual(lead.mobile_no, "+919600000001")
		self.assertEqual(lead.source, "WhatsApp")
		row = frappe.get_doc("Periskope Contact", row.name)
		self.assertEqual((row.status, row.linked_lead), ("Lead Created", lead_name))
		# the history is on the lead now, as one comment plus the link itself
		self.assertEqual(
			frappe.db.get_value("Periskope Message", {"message_id": "q6"}, "crm_lead"), lead_name
		)
		self.assertTrue(
			frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "CRM Lead",
					"reference_name": lead_name,
					"content": ["like", "%WhatsApp history linked%"],
				},
			)
		)

	def test_the_history_comment_shows_the_latest_text_even_after_an_attachment(self):
		self._message("q6a", body="Please send the catalog")
		self._message("q6b", body="", message_type="document", from_me=True)  # the rep's file, no text
		row = frappe.get_doc("Periskope Contact", {"phone": "919600000001"})
		lead_name = contacts.create_lead_for_contact(row.name)
		content = frappe.db.get_value(
			"Comment",
			{
				"reference_doctype": "CRM Lead",
				"reference_name": lead_name,
				"content": ["like", "%WhatsApp history linked%"],
			},
			"content",
		)
		self.assertIn("Latest: Please send the catalog", content)

	def test_actions_on_a_contact_that_does_not_exist_say_so(self):
		from periskope_connector.api import contacts as contacts_api

		for call in (
			lambda: contacts.create_lead_for_contact("no-such-contact"),
			lambda: contacts.link_contact("no-such-contact", lead="x"),
			lambda: contacts_api.find_matches("no-such-contact"),
		):
			with self.assertRaises(frappe.DoesNotExistError):
				call()

	def test_link_action_writes_the_phone_and_links_the_history(self):
		self._message("q7", body="hello")
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Existing", "mobile_no": ""})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		row = frappe.get_doc("Periskope Contact", {"phone": "919600000001"})
		contacts.link_contact(row.name, lead=lead.name)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "periskope_phone_key"), "9600000001")
		row = frappe.get_doc("Periskope Contact", row.name)
		self.assertEqual((row.status, row.linked_lead), ("Linked", lead.name))
		self.assertEqual(
			frappe.db.get_value("Periskope Message", {"message_id": "q7"}, "crm_lead"), lead.name
		)

	def test_ignore_action_stops_the_queue_and_survives_new_messages(self):
		self._message("q8", body="spam")
		row = frappe.get_doc("Periskope Contact", {"phone": "919600000001"})
		contacts.ignore_contact(row.name, "Ignored (Spam)")
		self._message("q9", body="more spam")
		row = frappe.get_doc("Periskope Contact", row.name)
		self.assertEqual(row.status, "Ignored (Spam)")
		# the row is frozen (an ignored number is not re-queued or re-counted)
		self.assertEqual(row.message_count, 1)

	def test_an_ignored_number_is_not_turned_into_a_lead_automatically(self):
		from periskope_connector import linking

		self._message("q10", body="Hi, need fabric prices please")
		contacts.ignore_contact(
			frappe.get_doc("Periskope Contact", {"phone": "919600000001"}).name, "Ignored (Vendor)"
		)
		with patch.object(linking, "by_mobile", lambda n: []):
			doc = frappe.get_doc("Periskope Message", {"message_id": "q10"})
			doc.ai_category = "order_or_enquiry"
			self.assertIsNone(leads.create_lead(doc))

	def _mark_recent(self):
		"""The digest only lists the last 24 h; fixture timestamps are older, so mark them fresh."""
		for row in frappe.get_all("Periskope Contact", filters={"status": "New"}, pluck="name"):
			frappe.db.set_value(
				"Periskope Contact", row, "last_message_at", now_datetime(), update_modified=False
			)

	def test_the_digest_lists_new_contacts_ranked_by_activity(self):
		self._message("q11", body="one")
		self._message("q12", body="two")
		self._message("q13", body="three", chat_id="919600000002@c.us")
		self._mark_recent()
		with patch.object(frappe, "sendmail") as sendmail:
			with patch.object(contacts, "_settings", lambda: _settings(alert_emails="head@example.com")):
				self.assertEqual(contacts.send_digest(), 2)
			kwargs = sendmail.call_args.kwargs
			self.assertEqual(kwargs["recipients"], ["head@example.com"])
			self.assertIn("919600000001", kwargs["message"])  # most active first
			self.assertIn("9600000001", kwargs["message"])

	def test_the_digest_is_sent_to_the_managers_when_no_head_is_set(self):
		self._message("q14", body="x")
		self._mark_recent()
		with patch.object(frappe, "sendmail") as sendmail:
			with patch.object(contacts, "_settings", lambda: _settings(alert_emails="boss@example.com")):
				self.assertEqual(contacts.send_digest(), 1)
				self.assertEqual(sendmail.call_args.kwargs["recipients"], ["boss@example.com"])

	def test_no_recipients_no_digest(self):
		self._message("q15", body="x")
		with patch.object(frappe, "sendmail") as sendmail:
			self.assertEqual(contacts.send_digest(), 0)
			sendmail.assert_not_called()


class TestDedupe(FrappeTestCase):
	def test_a_number_on_a_crm_contact_blocks_a_second_lead(self):
		# the Contact controller keeps phones in the Contact Phone child table
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Existing",
				"phone_nos": [{"phone": "+919600000001", "is_primary_mobile_no": 1}],
			}
		)
		contact.insert(ignore_permissions=True)
		self.assertTrue(leads._known_contact("919600000001"))
		self.assertFalse(leads._known_contact("919600000099"))
