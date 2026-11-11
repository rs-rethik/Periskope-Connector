"""Tests for the shared read state (read_state.py): a chat is read for everyone, and the customer sees
blue ticks, only when its assigned sales person or a member of its team opens it; a manager or admin
looking at it changes nothing."""

import json
from unittest.mock import patch

import frappe

from periskope_connector import numbers, read_state, safety
from periskope_connector.api import chat_actions, inbox
from periskope_connector.tests.test_inbox import A, InboxCase, _agent, _user
from periskope_connector.tests.utils import settings


class ReadCase(InboxCase):
	def setUp(self):
		super().setUp()
		self.rep = _user("read.rep@example.com", "Sales User")
		self.other = _user("read.other@example.com", "Sales User")
		self.manager = _user("read.manager@example.com", "Sales Manager")
		numbers.ensure(A)
		_agent(self.rep, A)
		_agent(self.other, A)
		self._msg()
		self.chat = self._chat()
		settings(test_mode=0)  # live: in test mode no receipt goes out (TestReceipt)
		self.queued = []
		p = patch.object(frappe, "enqueue", lambda method, **kw: self.queued.append(kw.get("chat")))
		p.start()
		self.addCleanup(p.stop)

	def _assign(self, *users, team=None):
		frappe.db.set_value("Periskope Chat", self.chat, {"_assign": json.dumps(list(users)), "team": team})

	def _open(self, user):
		frappe.set_user(user)
		inbox.mark_seen(self.chat)
		frappe.set_user("Administrator")

	def _unread(self):
		return inbox.get_chat(self.chat).unread


class TestSharedRead(ReadCase):
	def test_a_manager_opening_it_leaves_it_unread(self):
		self._assign(self.rep)
		self._open(self.manager)
		self.assertTrue(self._unread())
		self.assertEqual(self.queued, [])  # no blue ticks for the customer

	def test_the_assignee_makes_it_read_for_everyone_and_the_customer(self):
		self._assign(self.rep)
		self._open(self.rep)
		self.assertFalse(self._unread())  # also for the Administrator looking now
		self.assertEqual(self.queued, [self.chat])
		with patch("periskope_connector.client.request") as request:
			read_state.send_receipt(self.chat)  # the queued job: the customer now sees blue ticks
		request.assert_called_once()
		self._open(self.rep)  # nothing new since: no second receipt
		self.assertEqual(self.queued, [self.chat])
		# a new customer message is unread again
		frappe.db.set_value(
			"Periskope Chat",
			self.chat,
			{
				"last_incoming_at": frappe.utils.add_to_date(None, minutes=1),
				"last_message_direction": "Incoming",
			},
		)
		self.assertTrue(self._unread())

	def test_another_rep_does_not_count_but_a_team_member_does(self):
		self._assign(self.rep)
		self.assertFalse(read_state.opened(self.chat, self.other))
		self.assertTrue(self._unread())
		team = frappe.get_doc(
			{
				"doctype": "Periskope Team",
				"team_name": f"Read team {frappe.generate_hash(length=4)}",
				"members": [{"user": self.other}],
			}
		)
		team.flags.ignore_mandatory = True
		team.insert(ignore_permissions=True)
		self._assign(self.rep, team=team.name)
		self.assertTrue(read_state.opened(self.chat, self.other))
		self.assertFalse(self._unread())

	def test_an_unassigned_chat_is_read_by_a_sales_user_not_a_manager(self):
		# (a rep sees an unassigned chat only through its lead or team; the rule is checked directly)
		self._assign()
		self.assertFalse(read_state.opened(self.chat, self.manager))
		self.assertTrue(self._unread())
		self.assertTrue(read_state.opened(self.chat, self.rep))
		self.assertFalse(self._unread())

	def test_mark_as_read_works_for_a_manager_without_blue_ticks(self):
		self._assign(self.rep)
		frappe.set_user(self.manager)
		chat_actions.mark_read([self.chat])
		frappe.set_user("Administrator")
		self.assertFalse(self._unread())
		self.assertEqual(self.queued, [])
		# the chat is read in the inbox now, but its own rep opening it still shows the customer
		self._open(self.rep)
		self.assertEqual(self.queued, [self.chat])

	def test_a_receipt_that_failed_is_sent_when_the_chat_is_opened_again(self):
		self._assign(self.rep)
		self._open(self.rep)
		with patch("periskope_connector.client.request", side_effect=RuntimeError("down")):
			read_state.send_receipt(self.chat)  # logged, never raised
		self.assertFalse(frappe.db.get_value("Periskope Chat", self.chat, "customer_read_at"))
		self._open(self.rep)
		self.assertEqual(self.queued, [self.chat, self.chat])

	def test_no_receipt_is_queued_in_test_mode(self):
		settings(test_mode=1, test_allowed_chats="")
		self._assign(self.rep)
		self._open(self.rep)
		self.assertFalse(self._unread())
		self.assertEqual(self.queued, [])


class TestReceipt(ReadCase):
	def test_periskope_mark_read_only_for_allowed_chats_in_test_mode(self):
		with (
			patch.object(safety, "send_mode", return_value=safety.LIVE),
			patch("periskope_connector.client.request") as request,
		):
			read_state.send_receipt(self.chat)
		chat_id = frappe.db.get_value("Periskope Chat", self.chat, "chat_id")
		request.assert_called_once_with("POST", f"chats/{chat_id}/read", phone=A, text=True)
		self.assertTrue(frappe.db.get_value("Periskope Chat", self.chat, "customer_read_at"))
		with (
			patch.object(safety, "send_mode", return_value=safety.DRY_RUN),
			patch("periskope_connector.client.request") as request,
		):
			read_state.send_receipt(self.chat)
		request.assert_not_called()
