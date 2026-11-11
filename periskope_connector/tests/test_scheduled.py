"""Scheduled messages (scheduled.py, api/scheduled.py): scheduled in Periskope from a chat, Periskope
numbers only, repeating for managers only, test mode refused and paused, edited by who made it or a
manager, and Periskope's list and runs read back. Periskope is never called for real."""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from periskope_connector import scheduled
from periskope_connector.api import scheduled as api
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.test_crm_panel import CUSTOMER, A, PanelCase
from periskope_connector.tests.utils import settings

REP = "pkt.sched.rep@example.com"
OTHER = "pkt.sched.other@example.com"
STRANGER = "919600055551"


def _later(hours=2) -> str:
	return str(add_to_date(now_datetime(), hours=hours))


class ScheduledCase(PanelCase):
	def setUp(self):
		super().setUp()
		settings(
			enabled=1, test_mode=1, test_allowed_chats=CUSTOMER, base_url="https://api.periskope.test/v1"
		)
		_user(REP, "Sales User")
		_user(OTHER, "Sales User")
		self.chat = self._msg()
		self.stranger_chat = self._msg(contact=STRANGER)
		for chat in (self.chat, self.stranger_chat):
			frappe.db.set_value("Periskope Chat", chat, "_assign", frappe.as_json([REP, OTHER]))
		self.calls, self.listed, self.runs = [], [], []
		self.n = 0

		def fake(method, path, phone=None, json=None, params=None, missing_ok=False, **kw):
			self.calls.append((method, path, phone, json))
			if method == "POST" and path == "message/scheduled":
				self.n += 1
				return self._item(f"S{self.n}", json)
			if method == "GET" and path == "message/scheduled":
				return {"scheduled_messages": self.listed}
			if path.endswith("/occurrences"):
				return {"occurrences": self.runs}
			if method == "GET" and path.startswith("message/scheduled/"):
				sid = path.rsplit("/", 1)[1]
				return self._item(
					sid, {"chat_id": f"{CUSTOMER}@c.us"}, status=self.status.get(sid, "scheduled")
				)
			return {}

		self.status = {}
		for target in (scheduled, api):
			p = patch.object(target, "request", side_effect=fake)
			p.start()
			self.addCleanup(p.stop)

	def _item(self, sid, body, status="scheduled"):
		return {
			"scheduled_id": sid,
			"org_phone": f"{A}@c.us",
			"chat_id": body.get("chat_id"),
			"message_payload": {"message": body.get("message", "hello")},
			"scheduled_at": body.get("scheduled_at") or "2030-01-01T09:00:00.000Z",
			"is_repeat": bool(body.get("is_repeat")),
			"repeat_config": body.get("repeat_config"),
			"status": status,
			"performed_by": "api",
		}

	def _as(self, user):
		frappe.set_user(user)


class TestSchedule(ScheduledCase):
	def test_a_rep_schedules_a_one_time_message_to_a_test_number(self):
		self._as(REP)
		row = api.schedule(self.chat, _later(), text="Your samples ship today")
		method, path, phone, body = self.calls[-1]
		self.assertEqual((method, path, phone), ("POST", "message/scheduled", A))
		self.assertEqual(body["chat_id"], f"{CUSTOMER}@c.us")
		self.assertTrue(body["scheduled_at"].endswith("Z"))
		self.assertNotIn("repeat_config", body)
		self.assertEqual((row.scheduled_by, row.status, row.can_edit), (REP, "Scheduled", True))
		self.assertEqual(len(api.for_chat(self.chat)["scheduled"]), 1)

	def test_test_mode_refuses_other_customers(self):
		with self.assertRaises(frappe.ValidationError):
			api.schedule(self.stranger_chat, _later(), text="hi")
		self.assertEqual(self.calls, [])

	def test_the_meta_number_and_a_past_time_are_refused(self):
		with self.assertRaises(frappe.ValidationError):
			api.schedule(self.chat, _later(-1), text="late")
		frappe.db.set_value("WhatsApp Number", A, "source", "Meta")
		with self.assertRaises(frappe.ValidationError):
			api.schedule(self.chat, _later(), text="hi")
		self.assertEqual(self.calls, [])

	def test_repeating_is_for_managers(self):
		repeat = {"interval": "day", "days": ["monday", "friday", "funday"], "ends": _later(500)}
		self._as(REP)
		with self.assertRaises(frappe.PermissionError):
			api.schedule(self.chat, _later(), text="weekly", repeat=frappe.as_json(repeat))
		frappe.set_user("Administrator")
		row = api.schedule(self.chat, _later(), text="weekly", repeat=frappe.as_json(repeat))
		config = self.calls[-1][3]["repeat_config"]
		self.assertEqual((config["repeat_interval"], config["repeat_days"]), ("day", ["monday", "friday"]))
		self.assertTrue(row.is_repeat)


class TestChange(ScheduledCase):
	def test_only_who_made_it_or_a_manager_changes_it(self):
		self._as(REP)
		row = api.schedule(self.chat, _later(), text="hi")
		self._as(OTHER)
		with self.assertRaises(frappe.PermissionError):
			api.update(row.name, paused=1)
		with self.assertRaises(frappe.PermissionError):
			api.delete(row.name)
		frappe.set_user("Administrator")
		self.status[row.name] = "paused"
		self.assertEqual(api.update(row.name, paused=1).status, "Paused")
		self.assertIn(("PATCH", f"message/scheduled/{row.name}", None, {"paused": True}), self.calls)
		api.delete(row.name)
		self.assertFalse(frappe.db.exists(scheduled.DOCTYPE, row.name))

	def test_a_schedule_to_a_customer_is_not_resumed_in_test_mode(self):
		scheduled.save(self._item("X1", {"chat_id": f"{STRANGER}@c.us"}, status="paused"))
		with self.assertRaises(frappe.ValidationError):
			api.update("X1", paused=0)


class TestSync(ScheduledCase):
	def test_test_mode_pauses_the_schedules_to_customers(self):
		scheduled.save(self._item("T1", {"chat_id": f"{STRANGER}@c.us"}))
		scheduled.save(self._item("T2", {"chat_id": f"{CUSTOMER}@c.us"}))
		self.assertEqual(scheduled.pause_for_test_mode(), 1)
		row = frappe.db.get_value(scheduled.DOCTYPE, "T1", ["status", "paused_for_test_mode"], as_dict=True)
		self.assertEqual((row.status, row.paused_for_test_mode), ("Paused", 1))
		self.assertEqual(frappe.db.get_value(scheduled.DOCTYPE, "T2", "status"), "Scheduled")

	def test_the_list_and_the_runs_are_read_back(self):
		self._as(REP)
		mine = api.schedule(self.chat, _later(), text="hi").name
		frappe.set_user("Administrator")
		self.listed = [self._item("CONSOLE", {"chat_id": f"{CUSTOMER}@c.us", "message": "from the console"})]
		self.runs = [
			{"is_success": False, "remarks": "phone_not_found", "created_at": "2030-01-01T09:00:00Z"}
		]
		frappe.db.set_value(scheduled.DOCTYPE, mine, "runs_checked_to", "2000-01-01")
		with patch.object(scheduled, "_bell") as bell:
			scheduled.check_runs(mine)
		bell.assert_called_once()
		self.assertEqual(frappe.db.get_value(scheduled.DOCTYPE, mine, "last_failure"), "phone_not_found")
		scheduled.sync()
		self.assertTrue(frappe.db.exists(scheduled.DOCTYPE, "CONSOLE"))
		self.assertFalse(frappe.db.exists(scheduled.DOCTYPE, mine))  # gone from Periskope's list

	def test_turning_test_mode_on_pauses_them(self):
		settings(test_mode=0)
		doc = frappe.get_doc("Periskope Settings")
		doc.test_mode = 1
		with patch("frappe.enqueue") as enqueue:
			doc.save(ignore_permissions=True)
		self.assertIn(
			"periskope_connector.scheduled.on_test_mode_on",
			[c.args[0] for c in enqueue.call_args_list if c.args],
		)
