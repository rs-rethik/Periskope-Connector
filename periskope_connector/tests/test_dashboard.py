"""Tests for the managers' dashboard of the WhatsApp inbox (phase f): how a wait and its answer are
measured, SLA compliance, what is left out, the filters and who may see it."""

from datetime import timedelta

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, now_datetime

from periskope_connector import chat, numbers
from periskope_connector.api import dashboard
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

A = "919600011111"
B = "919600022222"


class DashboardCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_dashboard_test")
		self.addCleanup(frappe.set_user, "Administrator")
		numbers.ensure(A)
		numbers.ensure(B)
		self.rep = "dash.rep@example.com"
		if not frappe.db.exists("User", self.rep):
			frappe.get_doc(
				{"doctype": "User", "email": self.rep, "first_name": "Dash", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", self.rep).add_roles("Sales User")
		# A day in the past nobody else's test data uses.
		self.day = get_datetime("2031-03-04 10:00:00")
		self.n = 0

	def _chat(self, contact, number=A, policy=None, is_group=0):
		name = chat.name_of(number, f"{contact}@c.us")
		if name:
			return name
		doc = frappe.get_doc(
			{
				"doctype": "Periskope Chat",
				"chat_id": f"{contact}@{'g' if is_group else 'c'}.us",
				"whatsapp_number": number,
				"org_phone": number,
				"is_group": is_group,
				"sla_policy": policy,
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		return doc.name

	def _m(self, chat_name, minutes, direction="Incoming", **kw):
		self.n += 1
		row = frappe.db.get_value(
			"Periskope Chat", chat_name, ["chat_id", "whatsapp_number", "is_group"], as_dict=True
		)
		frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": f"dash_{self.n}_{frappe.generate_hash(length=6)}",
				"periskope_chat": chat_name,
				"chat_id": row.chat_id,
				"whatsapp_number": row.whatsapp_number,
				"org_phone": row.whatsapp_number,
				"is_group": row.is_group,
				"direction": direction,
				"message_type": "text",
				"body": "x",
				"message_timestamp": self.day + timedelta(minutes=minutes),
				**kw,
			}
		).insert(ignore_permissions=True)

	def _board(self, **kw):
		return dashboard.get_dashboard(from_date="2031-03-04", to_date="2031-03-04", **kw)


class TestWaits(DashboardCase):
	def test_a_wait_runs_from_the_first_message_to_the_first_real_answer(self):
		policy = frappe.get_doc(
			{
				"doctype": "Periskope SLA Policy",
				"policy_name": "Dash 5m",
				"first_response_minutes": 5,
				"use_working_hours": 0,
			}
		).insert(ignore_permissions=True)
		c = self._chat("919600080001", policy=policy.name)
		self._m(c, 0)  # the customer starts waiting
		self._m(c, 1)  # still the same wait
		self._m(c, 3, "Outgoing", agent_reply="Holding")  # not an answer
		self._m(c, 10, "Outgoing", sent_by=self.rep)  # answered after 10 minutes: late for 5m
		self._m(c, 20)  # a new wait
		self._m(c, 22, "Outgoing", agent_reply="Answer")  # the AI answers in 2 minutes
		d = self._board()
		self.assertEqual((d["response"]["waits"], d["response"]["answered"]), (2, 2))
		self.assertEqual(d["response"]["median_seconds"], 360)  # 600 and 120
		self.assertEqual(d["response"]["sla_percent"], 50)
		reps = {r["key"]: r for r in d["by_rep"]}
		self.assertEqual(reps[self.rep]["avg_seconds"], 600)
		self.assertEqual(reps["AI assistant"]["avg_seconds"], 120)
		self.assertIn("Dash", [r["name"] for r in d["leaderboard"]])

	def test_group_chats_and_unanswered_waits(self):
		group = self._chat("120363000000001", is_group=1)
		self._m(group, 0)
		self._m(group, 5, "Outgoing", sent_by=self.rep)
		waiting = self._chat("919600080002")
		self._m(waiting, 30)
		d = self._board()
		self.assertEqual((d["response"]["waits"], d["response"]["answered"]), (1, 0))  # only the 1-to-1 chat

	def test_volume_tags_and_the_number_filter(self):
		on_a = self._chat("919600080003", number=A)
		on_b = self._chat("919600080004", number=B)
		self._m(on_a, 0, ai_category=frappe.get_all("Periskope Tag", pluck="name", limit=1)[0])
		self._m(on_a, 5, "Outgoing", sent_by=self.rep)
		self._m(on_b, 0)
		d = self._board()
		day = next(x for x in d["per_day"] if x["day"] == "2031-03-04")
		self.assertEqual((day["received"], day["sent"]), (2, 1))
		self.assertEqual(sum(t.chats for t in d["tags"]), 1)
		only_b = self._board(number=B)
		self.assertEqual((only_b["tiles"]["received"], only_b["tiles"]["sent"]), (1, 0))
		self.assertEqual([n["number"] for n in only_b["by_number"]], [B])

	def test_only_managers_see_it(self):
		frappe.set_user(self.rep)
		self.assertRaises(frappe.PermissionError, self._board)

	def test_a_breached_chat_is_listed(self):
		c = self._chat("919600080005")
		frappe.db.set_value(
			"Periskope Chat",
			c,
			{
				"waiting_since": now_datetime() - timedelta(hours=2),
				"response_due": now_datetime() - timedelta(hours=1),
			},
		)
		d = dashboard.get_dashboard()
		self.assertIn(c, [b["name"] for b in d["breaches"]])
		self.assertGreaterEqual(d["tiles"]["breached"], 1)
