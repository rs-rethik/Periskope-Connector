"""Tests for the Periskope Sla Level doctype and the code that works on it."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import messages, reminders
from periskope_connector.tests.utils import at, cfg

CHAT = "919600000055@c.us"


def level(minutes, action, severity="Low", recipients=""):
	return SimpleNamespace(wait_minutes=minutes, min_severity=severity, action=action, recipients=recipients)


class TestSlaLevels(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for p in (
			patch.object(frappe.db, "commit"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			p.start()
			self.addCleanup(p.stop)
		self.sendmail = patch.object(frappe, "sendmail").start()
		self.addCleanup(patch.stopall)
		frappe.db.savepoint("periskope_sla_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_sla_test")
		self.seq = 0

	def _use(self, *levels):
		config = cfg(levels=list(levels))
		patch.object(reminders, "get_config", lambda *a, **k: config).start()

	def _add(self, when, severity=None, body="Please send the price list"):
		self.seq += 1
		name = messages.create_message(
			{
				"event_type": "message.created",
				"data": {
					"message_id": f"sla_{self.seq}",
					"chat_id": CHAT,
					"from_me": False,
					"org_phone": "919786387999@c.us",
					"message_type": "chat",
					"body": body,
					"timestamp": "2031-01-01T00:00:00Z",
				},
			}
		)
		frappe.db.set_value("Periskope Message", name, "message_timestamp", when)
		if severity:
			frappe.db.set_value("Periskope Message", name, "severity", severity)

	def _reminder(self):
		return frappe.get_all(
			"Periskope Reply Reminder",
			{"chat_id": CHAT},
			["name", "status", "level", "todo", "fired_rows"],
		)

	def test_a_single_row_does_exactly_what_it_says(self):
		self._use(level(30, reminders.EMAIL_RECIPIENTS, recipients="qa@example.com"))
		self._add(at(0, 9))
		self.assertEqual(reminders.run(at(0, 9, 20))["escalated"], 0)  # not yet
		reminders.run(at(0, 9, 40))
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], ["qa@example.com"])
		row = self._reminder()[0]
		self.assertFalse(row.todo)  # no ToDo row, so no ToDo
		self.assertEqual((row.status, row.level), ("Escalated", 2))
		self.sendmail.reset_mock()
		reminders.run(at(0, 12))
		self.sendmail.assert_not_called()  # once per waiting period

	def test_a_row_applies_from_its_severity_up(self):
		self._use(level(10, reminders.NOTIFY_REP, "Medium"))
		self._add(at(0, 9), severity="Low")
		reminders.run(at(0, 9, 30))
		self.assertEqual(self._reminder(), [])
		self._add(at(0, 9, 5), severity="Medium")
		reminders.run(at(0, 9, 30))
		self.assertEqual(len(self._reminder()), 1)

	def test_a_later_row_with_the_same_action_is_not_repeated(self):
		self._use(
			level(10, reminders.EMAIL_MANAGERS),
			level(60, reminders.EMAIL_MANAGERS),
			level(60, reminders.EMAIL_RECIPIENTS, recipients="head@example.com"),
		)
		self._add(at(0, 9))
		reminders.run(at(0, 9, 20))
		reminders.run(at(0, 10, 30))
		recipients = [call.kwargs["recipients"] for call in self.sendmail.call_args_list]
		self.assertEqual(recipients, [["boss@example.com"], ["head@example.com"]])

	def test_a_failed_email_is_tried_again_next_run(self):
		self._use(level(10, reminders.EMAIL_MANAGERS))
		self._add(at(0, 9))
		self.sendmail.side_effect = RuntimeError("mail down")
		with patch.object(frappe, "log_error"):
			reminders.run(at(0, 9, 20))
		self.assertNotIn(reminders.EMAIL_MANAGERS, (self._reminder()[0].fired_rows or ""))
		self.sendmail.side_effect = None
		reminders.run(at(0, 9, 40))
		self.assertEqual(self.sendmail.call_count, 2)
		self.assertIn(reminders.EMAIL_MANAGERS, self._reminder()[0].fired_rows)

	def test_the_steps_done_are_read_from_the_reminder(self):
		reminder = SimpleNamespace(fired_rows="Notify Rep|\nEmail Managers|")
		self.assertEqual(reminders._done(reminder), {"Notify Rep|", "Email Managers|"})
		self.assertEqual(reminders._done(SimpleNamespace(fired_rows="")), set())

	def test_the_ladder_is_read_in_order_of_waiting_time(self):
		frappe.db.delete("Periskope SLA Level")
		for minutes, severity, action in ((120, "Low", "Email Managers"), (15, "High", "Notify Rep")):
			frappe.get_doc(
				{
					"doctype": "Periskope SLA Level",
					"wait_minutes": minutes,
					"min_severity": severity,
					"action": action,
				}
			).insert()
		config = reminders.get_config()
		self.assertEqual([row.action for row in config.levels], ["Notify Rep", "Email Managers"])

	def test_an_empty_table_means_no_actions(self):
		self._use()
		self._add(at(0, 9), severity="High")
		reminders.run(at(0, 12))
		self.assertEqual(self._reminder(), [])

	def test_install_creates_the_usual_ladder_once(self):
		from periskope_connector import install

		frappe.db.delete("Periskope SLA Level")
		install._ensure_sla_levels()
		install._ensure_sla_levels()
		rows = frappe.get_all(
			"Periskope SLA Level",
			fields=["wait_minutes", "min_severity", "action"],
			order_by="wait_minutes asc, creation asc",
		)
		self.assertEqual(len(rows), 8)
		self.assertEqual((rows[0].wait_minutes, rows[0].min_severity), (30, "High"))
		self.assertIn((60, "Notify Rep"), [(r.wait_minutes, r.action) for r in rows])

	def test_a_bad_level_is_refused(self):
		for values in (
			{"wait_minutes": 10, "action": "Email Recipients", "recipients": "nope"},
			{"wait_minutes": -5, "action": "Notify Rep"},
		):
			with self.assertRaises(frappe.ValidationError, msg=str(values)):
				frappe.get_doc({"doctype": "Periskope SLA Level", **values}).insert()
