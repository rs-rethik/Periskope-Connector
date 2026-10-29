"""Tests for the Today page (api/today.py): who is waiting on the signed-in rep, their tasks due, their
leads and deals going quiet, and the numbers that wrote for the first time today."""

import json

import frappe
from frappe.utils import add_days, now_datetime, today

from periskope_connector.api import today as today_api
from periskope_connector.tests.test_inbox import CUSTOMER, OTHER, InboxCase
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


class TestToday(InboxCase):
	def _names(self, section):
		return [r.name for r in today_api.get_today()[section]["rows"]]

	def test_a_customer_who_wrote_last_waits_until_someone_answers(self):
		self._msg(body="Need 30s cotton")
		chat = self._chat()
		self.assertIn(chat, self._names("waiting"))
		self._msg(body="Sharing the price now", from_me=True)
		self.assertNotIn(chat, self._names("waiting"))

	def test_a_chat_of_someone_else_or_a_group_is_not_waiting_on_me(self):
		self._msg()
		self._msg(contact=OTHER)
		frappe.db.set_value(
			"Periskope Chat", self._chat(), "_assign", json.dumps(["someone.else@example.com"])
		)
		frappe.db.set_value("Periskope Chat", self._chat(contact=OTHER), "is_group", 1)
		waiting = self._names("waiting")
		self.assertNotIn(self._chat(), waiting)
		self.assertNotIn(self._chat(contact=OTHER), waiting)

	def test_a_number_that_wrote_today_is_new(self):
		self._msg(contact=CUSTOMER)
		self.assertIn(self._chat(), self._names("new"))

	def test_my_overdue_task_is_due_and_can_be_marked_done(self):
		task = frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "Send the revised quote",
				"assigned_to": "Administrator",
				"status": "Todo",
				"due_date": f"{add_days(today(), -2)} 10:00:00",
			}
		).insert(ignore_permissions=True)
		tasks = today_api.get_today()["tasks"]
		self.assertIn(task.name, [r.name for r in tasks["rows"]])
		self.assertGreaterEqual(tasks["overdue"], 1)
		today_api.complete_task(task.name)
		self.assertEqual(frappe.db.get_value("CRM Task", task.name, "status"), "Done")
		self.assertNotIn(task.name, self._names("tasks"))

	def test_my_lead_with_no_activity_goes_quiet_until_the_customer_writes(self):
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Quiet", "lead_owner": "Administrator"}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"CRM Lead", lead.name, "modified", add_days(now_datetime(), -10), update_modified=False
		)
		self.assertIn(lead.name, self._names("quiet"))
		self._msg()
		frappe.db.set_value("Periskope Message", {"periskope_chat": self._chat()}, "crm_lead", lead.name)
		self.assertNotIn(lead.name, self._names("quiet"))
