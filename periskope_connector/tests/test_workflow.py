"""Tests for phase d of the WhatsApp inbox: statuses that reopen, teams with their assignment rule
and who sees what, SLA clocks and breach alerts, the bulk actions, saved views and the bell."""

import json
from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from periskope_connector import access, chat, messages, numbers, reminders, sla, workflow
from periskope_connector.api import chat_actions, inbox, notifications, views
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, make_sales_manager

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = [
	*OTHER_APP_DOCTYPES,
	"Periskope Team",
	"Periskope SLA Policy",
]

A = "919600011111"
B = "919600022222"


def _user(email, *roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.get_doc("User", email).add_roles(*roles)
	return email


class FlowCase(FrappeTestCase):
	def setUp(self):
		# Runs after the rollback below (clean-ups run last first): the teams' assignment rules are
		# gone again, so Frappe's cached list of rules must go too.
		from frappe.cache_manager import clear_doctype_map

		self.addCleanup(clear_doctype_map, "Assignment Rule", "Periskope Chat")
		self.mail = isolate(self, "periskope_flow_test")
		for p in (
			patch.object(messages, "fetch_chat_name", lambda *a, **k: ""),
			patch("periskope_connector.autoreply.on_message", lambda doc: ""),
		):
			p.start()
			self.addCleanup(p.stop)
		self.addCleanup(frappe.set_user, "Administrator")
		numbers.ensure(A)
		numbers.ensure(B)
		self.rep1 = _user("flow.rep1@example.com", "Sales User")
		self.rep2 = _user("flow.rep2@example.com", "Sales User")
		self.n = 0

	def _team(self, name="Sales A", assignment="Round Robin", see=1, members=None, number_list=(A,)):
		return frappe.get_doc(
			{
				"doctype": "Periskope Team",
				"team_name": name,
				"assignment": assignment,
				"team_can_see_team_chats": see,
				"members": [{"user": u} for u in (members or [self.rep1, self.rep2])],
				"numbers": [{"whatsapp_number": n} for n in number_list],
			}
		).insert(ignore_permissions=True)

	def _msg(self, contact, number=A, from_me=False, body="Price?", minutes_ago=0, agent_reply=None):
		self.n += 1
		stamp = (datetime.utcnow() - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
		patcher = patch.object(messages, "match_sent", lambda *a: agent_reply or "")
		with patcher:
			messages.create_message(
				event(
					message_id=f"{'true' if from_me else 'false'}_{contact}@c.us_F{self.n}{frappe.generate_hash(length=4)}",
					chat_id=f"{contact}@c.us",
					sender_phone=f"{(number if from_me else contact)}@c.us",
					org_phone=f"{number}@c.us",
					from_me=from_me,
					body=body,
					timestamp=stamp,
				)
			)
		return chat.name_of(number, f"{contact}@c.us")

	def _assigned(self, name):
		return json.loads(frappe.db.get_value("Periskope Chat", name, "_assign") or "[]")


class TestTeams(FlowCase):
	def test_new_chats_of_a_team_go_to_its_members_in_turn(self):
		team = self._team()
		self.assertTrue(team.assignment_rule)
		first = self._msg("919600070001")
		second = self._msg("919600070002")
		self.assertEqual(frappe.db.get_value("Periskope Chat", first, "team"), team.name)
		owners = self._assigned(first) + self._assigned(second)
		self.assertEqual(sorted(owners), sorted([self.rep1, self.rep2]))

	def test_a_manual_team_assigns_nobody_and_no_team_assigns_nobody(self):
		self._team(assignment="Manual")
		self.assertEqual(self._assigned(self._msg("919600070003")), [])
		self.assertEqual(self._assigned(self._msg("919600070004", number=B)), [])

	def test_members_see_every_chat_on_the_teams_numbers_and_no_other(self):
		self._team(assignment="Manual")
		name = self._msg("919600070005")
		other = self._msg("919600070006", number=B)
		self.assertTrue(access.can_see_chat(name, self.rep1))  # not assigned to them: on their number
		self.assertFalse(access.can_see_chat(other, self.rep1))
		self.assertEqual(access.allowed_numbers(self.rep1), [A])


class TestLifecycle(FlowCase):
	def test_a_customer_message_reopens_the_chat(self):
		name = self._msg("919600070010")
		workflow.set_status(name, "Resolved")
		self.assertTrue(frappe.db.get_value("Periskope Chat", name, "resolved_on"))
		self._msg("919600070010", body="One more thing")
		self.assertEqual(
			frappe.db.get_value("Periskope Chat", name, ["status", "resolved_on"]), ("Open", None)
		)
		self.assertIn("Reopened", [a.action for a in chat_actions.activity(name)])

	def test_the_reply_clock_stops_on_an_answer_but_not_on_a_holding_reply(self):
		frappe.get_doc(
			{
				"doctype": "Periskope SLA Policy",
				"policy_name": "Fast",
				"is_default": 1,
				"first_response_minutes": 30,
				"resolution_minutes": 240,
				"use_working_hours": 0,
			}
		).insert(ignore_permissions=True)
		name = self._msg("919600070011", minutes_ago=10)
		row = frappe.db.get_value(
			"Periskope Chat", name, ["waiting_since", "response_due", "resolution_due"], as_dict=True
		)
		self.assertEqual(
			get_datetime(row.response_due) - get_datetime(row.waiting_since), timedelta(minutes=30)
		)
		self.assertTrue(row.resolution_due)
		self.assertEqual(inbox.get_chat(name).sla, "On Track")
		self._msg("919600070011", from_me=True, body="We'll get back", agent_reply="Holding")
		self.assertTrue(frappe.db.get_value("Periskope Chat", name, "waiting_since"))
		self._msg("919600070011", from_me=True, body="It is 245 per kg")
		row = frappe.db.get_value(
			"Periskope Chat", name, ["waiting_since", "first_responded_on", "response_breached"], as_dict=True
		)
		self.assertEqual((row.waiting_since, row.response_breached), (None, 0))
		self.assertTrue(row.first_responded_on)

	def test_a_late_reply_is_breached_listed_and_alerted_once(self):
		frappe.get_doc(
			{
				"doctype": "Periskope SLA Policy",
				"policy_name": "Fast",
				"is_default": 1,
				"first_response_minutes": 5,
				"use_working_hours": 0,
			}
		).insert(ignore_permissions=True)
		manager = make_sales_manager(_user("flow.manager@example.com", "Sales Manager"))
		name = self._msg("919600070012", minutes_ago=30)
		self.assertEqual(inbox.get_chat(name).sla, "Breached")
		self.assertIn(name, [c.name for c in inbox.get_chats({"sla": "breached"})["chats"]])
		self.assertEqual(sla.check_breaches(), 1)
		self.assertEqual(sla.check_breaches(), 0)
		self.assertTrue(
			frappe.db.exists(
				"Notification Log", {"for_user": manager, "document_name": name, "type": "Alert"}
			)
		)
		self.assertIn("SLA Breached", [a.action for a in chat_actions.activity(name)])

	def test_working_hours_and_holidays_push_the_due_time(self):
		friday = datetime(2031, 9, 5, 18, 30)  # a Friday, 30 minutes before closing at 19:00
		self.assertEqual(friday.weekday(), 4)
		config = reminders.get_config()
		config.start, config.end = datetime(2000, 1, 1, 9).time(), datetime(2000, 1, 1, 19).time()
		config.days = reminders.WEEKDAYS["Monday to Saturday"]
		config.holidays = {datetime(2031, 9, 6).date()}  # Saturday is a holiday
		with patch.object(reminders, "get_config", lambda settings=None: config):
			self.assertEqual(sla.add_minutes(friday, 60, True), datetime(2031, 9, 8, 9, 30))  # Monday
			self.assertEqual(sla.add_minutes(friday, 60, False), datetime(2031, 9, 5, 19, 30))

	def test_holiday_list_dates_are_dates(self):
		if not frappe.db.exists("DocType", "Holiday List"):
			return
		doc = frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": "Periskope test holidays",
				"from_date": "2031-01-01",
				"to_date": "2031-12-31",
				"holidays": [{"holiday_date": "2031-10-20", "description": "Diwali"}],
			}
		).insert(ignore_permissions=True)
		dates = reminders._holiday_dates(doc.name)
		self.assertIn(datetime(2031, 10, 20).date(), dates)
		config = reminders.get_config()
		config.holidays = dates
		config.days = reminders.WEEKDAYS["Monday to Saturday"]
		config.start, config.end = datetime(2000, 1, 1, 9).time(), datetime(2000, 1, 1, 19).time()
		monday_11 = datetime(2031, 10, 20, 11, 0)
		self.assertEqual(monday_11.weekday(), 0)
		self.assertFalse(reminders.is_working_time(monday_11, config))  # the holiday is skipped
		self.assertTrue(reminders.is_working_time(monday_11 + timedelta(days=1), config))


class TestBulkActions(FlowCase):
	def setUp(self):
		super().setUp()
		frappe.get_doc(
			{"doctype": "Periskope Agent", "user": self.rep1, "numbers": [{"whatsapp_number": A}]}
		).insert(ignore_permissions=True)
		self.chats = [self._msg(f"91960007002{i}") for i in range(3)]
		self.elsewhere = self._msg("919600070029", number=B)  # on a number rep1 was not given

	def test_status_labels_and_read_marks_for_several_chats(self):
		chat_actions.set_status(self.chats, "Pending")
		self.assertEqual(
			{frappe.db.get_value("Periskope Chat", c, "status") for c in self.chats}, {"Pending"}
		)
		tag = frappe.get_all("Periskope Tag", pluck="name", limit=1)[0]
		chat_actions.add_label(self.chats, tag)
		self.assertEqual(inbox.get_chat(self.chats[0]).labels, [tag])
		self.assertEqual(inbox.get_chats({"label": tag, "status": "Pending"})["total"], 3)
		chat_actions.remove_label([self.chats[0]], tag)
		self.assertEqual(inbox.get_chat(self.chats[0]).labels, [])
		chat_actions.mark_read(self.chats)
		self.assertFalse(any(inbox.get_chat(c).unread for c in self.chats))

	def test_a_rep_assigns_only_to_people_on_the_number(self):
		chat_actions.assign([self.chats[0]], [self.rep1])  # a manager gives it to the rep
		outsider = _user("flow.outsider@example.com", "Sales User")
		frappe.set_user(self.rep1)
		self.assertRaises(frappe.PermissionError, chat_actions.assign, [self.chats[0]], [outsider])
		self.assertRaises(frappe.PermissionError, chat_actions.unassign, self.chats[0], "Administrator")
		self.assertRaises(frappe.PermissionError, chat_actions.set_team, [self.chats[0]], None)
		self.assertRaises(
			frappe.PermissionError, chat_actions.set_status, [self.elsewhere], "Resolved"
		)  # not on their number
		chat_actions.unassign(self.chats[0], self.rep1)
		self.assertEqual(self._assigned(self.chats[0]), [])

	def test_assign_to_me_and_the_assignment_bell(self):
		frappe.set_user(self.rep1)
		self.assertRaises(
			frappe.PermissionError, chat_actions.assign_to_me, [self.elsewhere]
		)  # cannot see it: not on their number
		frappe.set_user("Administrator")
		chat_actions.assign([self.chats[1]], [self.rep1])
		frappe.set_user(self.rep1)
		bell = notifications.get_notifications()
		self.assertIn(self.chats[1], [n.document_name for n in bell["notifications"]])
		notifications.mark_read()
		self.assertEqual(notifications.get_notifications()["unread"], 0)
		self.assertIn("Assigned", [a.action for a in chat_actions.activity(self.chats[1])])


class TestViews(FlowCase):
	def test_your_views_and_the_shared_ones(self):
		shared = views.save_view("Hot leads", {"status": "Open"}, ["title"], is_shared=1)
		frappe.set_user(self.rep2)
		views.save_view("Somebody else's", {})
		frappe.set_user(self.rep1)
		mine = views.save_view(
			"My unanswered", {"unanswered": 1}, ["title", "status"], group_by="status", is_shared=1
		)
		self.assertEqual(mine["is_shared"], 0)
		self.assertEqual({v["label"] for v in views.list_views()}, {"Hot leads", "My unanswered"})
		self.assertEqual(
			next(v for v in views.list_views() if v["label"] == "My unanswered")["filters"], {"unanswered": 1}
		)
		self.assertRaises(frappe.PermissionError, views.delete_view, shared["name"])
		views.delete_view(mine["name"])
		self.assertEqual([v["label"] for v in views.list_views()], ["Hot leads"])
