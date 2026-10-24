"""Tests for the WhatsApp inbox, phase a: one chat per (our number, contact), who may see which chat
(access.py, enforced through the permission hooks), the read API (api/inbox.py), live-update
recipients, the number sync and the migration patch."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import access, chat, inbox_realtime, messages, numbers
from periskope_connector.api import inbox
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

A = "919600011111"  # two of our numbers
B = "919600022222"
CUSTOMER = "919600033333"
OTHER = "919600044444"


def _user(email: str, *roles: str) -> str:
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		)
		user.flags.no_welcome_mail = True
		user.insert(ignore_permissions=True)
	user = frappe.get_doc("User", email)
	user.add_roles(*roles)
	return email


def _agent(user: str, *numbers_: str):
	frappe.get_doc(
		{
			"doctype": "Periskope Agent",
			"user": user,
			"numbers": [{"whatsapp_number": n} for n in numbers_],
		}
	).insert(ignore_permissions=True)


class InboxCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_inbox_test")
		for p in (
			patch.object(messages, "fetch_chat_name", lambda *a, **k: ""),
			patch("periskope_connector.autoreply.on_message", lambda doc: ""),
		):
			p.start()
			self.addCleanup(p.stop)
		self.addCleanup(frappe.set_user, "Administrator")
		self.n = 0

	def _msg(self, number=A, contact=CUSTOMER, body="Price of 30s cotton?", from_me=False, **kw):
		self.n += 1
		return messages.create_message(
			event(
				message_id=f"inbox_{self.n}_{frappe.generate_hash(length=6)}",
				chat_id=f"{contact}@c.us",
				sender_phone=f"{number if from_me else contact}@c.us",
				org_phone=f"{number}@c.us",
				from_me=from_me,
				body=body,
				**kw,
			)
		)

	def _chat(self, number=A, contact=CUSTOMER):
		return chat.name_of(number, f"{contact}@c.us")


class TestChatPerNumber(InboxCase):
	def test_the_same_contact_on_two_numbers_is_two_chats(self):
		first = self._msg(A, body="Hello on A")
		second = self._msg(B, body="Hello on B")
		on_a, on_b = self._chat(A), self._chat(B)
		self.assertTrue(on_a and on_b)
		self.assertNotEqual(on_a, on_b)
		self.assertEqual(frappe.db.get_value("Periskope Message", first, "periskope_chat"), on_a)
		self.assertEqual(frappe.db.get_value("Periskope Message", second, "periskope_chat"), on_b)
		self.assertEqual(frappe.db.get_value("Periskope Chat", on_a, "whatsapp_number"), A)
		# Both numbers became WhatsApp Number rows on their own.
		self.assertTrue(frappe.db.exists("WhatsApp Number", A) and frappe.db.exists("WhatsApp Number", B))

	def test_the_chat_row_keeps_the_list_preview(self):
		self._msg(body="Price of 30s cotton?")
		row = frappe.db.get_value(
			"Periskope Chat",
			self._chat(),
			["last_message_preview", "last_message_direction", "last_incoming_at", "message_count", "status"],
			as_dict=True,
		)
		self.assertEqual(row.last_message_preview, "Price of 30s cotton?")
		self.assertEqual((row.last_message_direction, row.message_count, row.status), ("Incoming", 1, "Open"))
		self.assertTrue(row.last_incoming_at)
		self._msg(body="It is 245 per kg", from_me=True)
		row = frappe.db.get_value(
			"Periskope Chat",
			self._chat(),
			["last_message_preview", "last_message_direction", "message_count"],
			as_dict=True,
		)
		self.assertEqual((row.last_message_direction, row.message_count), ("Outgoing", 2))

	def test_a_reply_goes_out_on_the_number_the_contact_last_used(self):
		self._msg(A, body="first on A")
		self._msg(B, body="later on B")
		frappe.db.set_value("Periskope Chat", self._chat(A), "last_message_at", "2020-01-01 10:00:00")
		frappe.db.set_value("Periskope Chat", self._chat(B), "last_message_at", now_datetime())
		self.assertEqual(chat.number_of(f"{CUSTOMER}@c.us"), B)

	def test_a_lead_shows_the_contact_on_every_number(self):
		self._msg(A)
		self._msg(B)
		for name in (self._chat(A), self._chat(B)):
			frappe.db.set_value("Periskope Chat", name, "crm_lead", "CRM-LEAD-TEST-1")
		self.assertEqual(chat.for_record("CRM Lead", "CRM-LEAD-TEST-1"), [f"{CUSTOMER}@c.us"])


class TestAccess(InboxCase):
	def setUp(self):
		super().setUp()
		self.manager = _user("inbox.manager@example.com", "Sales Manager")
		self.rep = _user("inbox.rep@example.com", "Sales User")
		self.stranger = _user("inbox.stranger@example.com", "Sales User")
		self._msg(A, CUSTOMER)  # assigned to the rep
		self._msg(A, OTHER)  # on the rep's number, not theirs
		self._msg(B, CUSTOMER)  # theirs by assignment, but on a number they do not have
		_agent(self.rep, A)
		_agent(self.stranger, B)
		for name in (self._chat(A, CUSTOMER), self._chat(B, CUSTOMER)):
			frappe.db.set_value("Periskope Chat", name, "_assign", json.dumps([self.rep]))

	def _visible(self, user):
		frappe.set_user(user)
		try:
			return set(frappe.get_list("Periskope Chat", pluck="name", limit=0))
		finally:
			frappe.set_user("Administrator")

	def test_a_manager_sees_every_chat_on_every_number(self):
		visible = self._visible(self.manager)
		for name in (self._chat(A, CUSTOMER), self._chat(A, OTHER), self._chat(B, CUSTOMER)):
			self.assertIn(name, visible)

	def test_a_rep_sees_only_their_chats_on_their_numbers(self):
		self.assertEqual(self._visible(self.rep), {self._chat(A, CUSTOMER)})

	def test_a_rep_sees_the_chats_of_the_leads_they_own(self):
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Owned", "lead_owner": self.rep, "mobile_no": f"+{OTHER}"}
		)
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Chat", self._chat(A, OTHER), "crm_lead", lead.name)
		self.assertIn(self._chat(A, OTHER), self._visible(self.rep))

	def test_a_user_who_is_no_agent_sees_nothing(self):
		nobody = _user("inbox.nobody@example.com", "Sales User")
		self.assertEqual(self._visible(nobody), set())
		self.assertEqual(access.allowed_numbers(nobody), [])

	def test_messages_follow_the_chat_rule(self):
		frappe.set_user(self.rep)
		try:
			chats = set(frappe.get_list("Periskope Message", pluck="periskope_chat", limit=0))
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(chats, {self._chat(A, CUSTOMER)})

	def test_one_record_reads_obey_the_rule_and_reps_cannot_write(self):
		mine = frappe.get_doc("Periskope Chat", self._chat(A, CUSTOMER))
		other = frappe.get_doc("Periskope Chat", self._chat(A, OTHER))
		self.assertTrue(frappe.has_permission("Periskope Chat", "read", mine, user=self.rep))
		self.assertFalse(frappe.has_permission("Periskope Chat", "read", other, user=self.rep))
		self.assertFalse(frappe.has_permission("Periskope Chat", "write", mine, user=self.rep))

	def test_guests_and_other_roles_cannot_use_the_inbox(self):
		if not frappe.db.exists("Role", "Inbox Test Role"):
			frappe.get_doc({"doctype": "Role", "role_name": "Inbox Test Role"}).insert(
				ignore_permissions=True
			)
		plain = _user("inbox.plain@example.com", "Inbox Test Role")
		frappe.set_user("Guest")
		self.assertRaises(frappe.PermissionError, inbox.get_chats)
		frappe.set_user(plain)
		self.assertRaises(frappe.PermissionError, inbox.counts)

	def test_live_updates_go_only_to_who_may_work_the_number(self):
		on_a = inbox_realtime._recipients(A)
		self.assertIn(self.rep, on_a)
		self.assertIn(self.manager, on_a)
		self.assertNotIn(self.stranger, on_a)


class TestInboxApi(InboxCase):
	def setUp(self):
		super().setUp()
		self.rep = _user("inbox.rep@example.com", "Sales User")
		numbers.ensure(A)
		_agent(self.rep, A)
		for i in range(3):
			self._msg(A, f"9196000555{i:02d}", body=f"Enquiry {i}")
		self._msg(B, CUSTOMER, body="On B")
		self.mine = self._chat(A, "919600055500")
		frappe.db.set_value("Periskope Chat", self.mine, "_assign", json.dumps([self.rep]))

	def test_filters_pages_and_counts(self):
		names = {self._chat(A, f"9196000555{i:02d}") for i in range(3)}
		on_a = inbox.get_chats({"number": A, "search": "Enquiry"})
		self.assertEqual({c.name for c in on_a["chats"]}, names)
		page = inbox.get_chats({"number": A, "search": "Enquiry"}, page_length=2)
		self.assertEqual((len(page["chats"]), page["total"]), (2, 3))
		row = on_a["chats"][0]
		self.assertTrue(row.unread)
		self.assertEqual(row.number_label, A)
		self.assertIn(A, inbox.counts()["unread_by_number"])

	def test_opening_a_chat_marks_it_read_for_that_user_only(self):
		inbox.mark_seen(self.mine)
		self.assertFalse(inbox.get_chat(self.mine).unread)
		frappe.set_user(self.rep)
		self.assertTrue(inbox.get_chat(self.mine).unread)

	def test_a_rep_reads_their_chat_and_nothing_else(self):
		frappe.set_user(self.rep)
		self.assertEqual([c.name for c in inbox.get_chats()["chats"]], [self.mine])
		self.assertEqual(len(inbox.get_messages(self.mine)["messages"]), 1)
		other = self._chat(A, "919600055501")
		self.assertRaises(frappe.PermissionError, inbox.get_messages, other)
		message = frappe.db.get_value("Periskope Message", {"periskope_chat": other})
		self.assertRaises(frappe.PermissionError, inbox.media, message)
		self.assertEqual([n["name"] for n in inbox.get_boot()["numbers"]], [A])

	def test_messages_never_carry_the_periskope_link(self):
		rows = inbox.get_messages(self.mine)["messages"]
		self.assertNotIn("media_path", rows[0])

	def test_older_messages_load_a_page_at_a_time(self):
		for i in range(4):
			self._msg(A, "919600055500", body=f"more {i}")
		latest = inbox.get_messages(self.mine, limit=3)
		self.assertTrue(latest["has_more"])
		self.assertEqual(latest["messages"][-1].body, "more 3")
		older = inbox.get_messages(self.mine, before=str(latest["messages"][0].message_timestamp), limit=10)
		self.assertTrue(
			all(m.message_timestamp < latest["messages"][0].message_timestamp for m in older["messages"])
		)


class TestNumbers(InboxCase):
	def test_sync_adds_numbers_and_tracks_disconnection(self):
		numbers.sync_from_periskope(
			[
				{
					"phone_id": "p1",
					"phone_name": "Sales A",
					"org_phone": f"{A}@c.us",
					"wa_state": "CONNECTED",
					"is_ready": True,
				}
			]
		)
		row = frappe.db.get_value(
			"WhatsApp Number", A, ["display_name", "is_ready", "disconnected_since"], as_dict=True
		)
		self.assertEqual((row.display_name, row.is_ready, row.disconnected_since), ("Sales A", 1, None))
		numbers.sync_from_periskope(
			[
				{
					"phone_id": "p1",
					"phone_name": "Sales A",
					"org_phone": f"{A}@c.us",
					"wa_state": "TIMEOUT",
					"is_ready": False,
				}
			]
		)
		self.assertTrue(frappe.db.get_value("WhatsApp Number", A, "disconnected_since"))
		numbers.sync_from_periskope([])
		self.assertEqual(frappe.db.get_value("WhatsApp Number", A, "wa_state"), "NOT LINKED")

	def test_a_number_is_saved_as_digits(self):
		self.assertEqual(numbers.ensure("+91 96000-11111@c.us"), A)


class TestMigrationPatch(InboxCase):
	def test_old_chats_are_split_per_number_and_messages_linked(self):
		from periskope_connector.patches import whatsapp_numbers_and_chats as patch_

		on_a = self._msg(A)
		on_b = self._msg(B)
		# Make it look like an earlier version: one chat row per contact, no number, no links.
		frappe.db.delete("Periskope Chat", {"chat_id": f"{CUSTOMER}@c.us"})
		frappe.db.sql(
			"""insert into `tabPeriskope Chat` (name, chat_id, org_phone, crm_lead, status)
			values (%s, %s, %s, 'CRM-LEAD-OLD', 'Open')""",
			(f"{CUSTOMER}@c.us", f"{CUSTOMER}@c.us", A),
		)
		frappe.db.sql(
			"update `tabPeriskope Message` set periskope_chat = null where name in (%s, %s)", (on_a, on_b)
		)
		patch_._split_chats()
		patch_._link_messages()
		patch_._split_chats()  # runs again without making doubles
		patch_._link_messages()
		self.assertEqual(frappe.db.count("Periskope Chat", {"chat_id": f"{CUSTOMER}@c.us"}), 2)
		self.assertEqual(frappe.db.get_value("Periskope Message", on_a, "periskope_chat"), f"{CUSTOMER}@c.us")
		new = frappe.db.get_value("Periskope Message", on_b, "periskope_chat")
		self.assertEqual(
			frappe.db.get_value("Periskope Chat", new, ["whatsapp_number", "crm_lead"]), (B, "CRM-LEAD-OLD")
		)
