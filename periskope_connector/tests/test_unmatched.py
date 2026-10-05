from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import linking, messages, unmatched
from periskope_connector.tests.test_linking import CHAT, _event


class TestUnmatched(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Kongu Region"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: "Meena"),
			patch.object(messages, "_rep_for_phone", lambda n: None),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_unmatched_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_unmatched_test")
		frappe.db.delete("Periskope Message")  # only this test's messages, rolled back afterwards

	def _rows(self):
		return unmatched.get_rows("2026-09-01", "2031-12-31")

	def test_an_unknown_number_is_listed_once_with_its_message_count_and_last_text(self):
		messages.create_message(_event(message_id="u1", body="first", timestamp="2026-09-29T06:00:00Z"))
		messages.create_message(_event(message_id="u2", body="second", timestamp="2026-09-29T07:00:00Z"))
		rows = self._rows()
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].kind, rows[0].who, rows[0].messages), ("Customer", "9600000001", 2))
		self.assertEqual((rows[0].name, rows[0].text), ("Meena", "second"))

	def test_a_number_added_to_the_crm_meanwhile_is_no_longer_listed(self):
		messages.create_message(_event(message_id="u3"))
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Meena", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		self.assertEqual(self._rows(), [])

	def test_team_groups_are_left_out_and_customer_groups_are_kept(self):
		messages.create_message(_event(message_id="u4", chat_id=CHAT))  # Internal: no customer to add
		self.assertEqual(self._rows(), [])
		frappe.db.set_value("Periskope Group", CHAT, "group_type", "Customer")
		rows = self._rows()
		self.assertEqual((rows[0].kind, rows[0].who), ("Group", CHAT))

	def test_messages_outside_the_dates_are_not_listed(self):
		messages.create_message(_event(message_id="u5", timestamp="2026-08-01T06:00:00Z"))
		self.assertEqual(self._rows(), [])

	def test_the_weekly_email_goes_to_the_managers_only_when_there_is_something_to_report(self):
		with (
			patch.object(frappe, "sendmail") as sendmail,
			patch("periskope_connector.reminders.get_config") as config,
			patch.object(unmatched, "get_rows") as rows,
		):
			config.return_value.emails = ["boss@example.com"]
			rows.return_value = []
			self.assertEqual(unmatched.send_weekly_report(), 0)
			sendmail.assert_not_called()

			rows.return_value = [
				frappe._dict(
					kind="Customer",
					who="9600000001",
					name="<b>x</b>",
					messages=2,
					last_message="2026-09-29 12:00:00",
					category="order_or_enquiry",
					sales_person=None,
					text="hi",
				)
			]
			self.assertEqual(unmatched.send_weekly_report(), 1)
			kwargs = sendmail.call_args.kwargs
			self.assertEqual(kwargs["recipients"], ["boss@example.com"])
			self.assertIn("9600000001", kwargs["message"])
			self.assertIn("&lt;b&gt;x&lt;/b&gt;", kwargs["message"])  # escaped

			config.return_value.emails = []
			sendmail.reset_mock()
			unmatched.send_weekly_report()
			sendmail.assert_not_called()
