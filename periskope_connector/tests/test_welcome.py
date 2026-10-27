"""Tests for the welcome to a new number and the lead filled from what the customer tells us."""

import random
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import autoreply, messages, reminders, safety, welcome
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

NUMBER = "919600011223"
CHAT = f"{NUMBER}@c.us"
OURS = "919786387999@c.us"
WELCOME = "Hi! Thanks for reaching out. May I have your name, company, city and email?"


def _utc(minutes: float = 0) -> str:
	return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class WelcomeCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_welcome_test")
		self.queued = []
		self.sent = []
		self.ai = {"welcome": WELCOME, "details": {}}
		for p in (
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
			patch.object(messages, "fetch_chat_name", lambda *a, **k: ""),
			patch.object(welcome, "_enqueue", lambda method, job_id, **kw: self.queued.append((method, kw))),
			patch.object(welcome, "send_message", side_effect=self._send),
			patch.object(autoreply, "send_message"),
			patch.object(welcome.assistant, "is_ready", return_value=True),
			patch.object(welcome.assistant, "complete_json", side_effect=self._ai),
		):
			p.start()
			self.addCleanup(p.stop)
		settings(enabled=1, enable_auto_reply=1, welcome_new_numbers=1, test_mode=0, reply_delay_minutes=15)
		for doctype in ("Periskope Message", "Periskope Auto Reply"):
			frappe.db.delete(doctype, {"contact_number": NUMBER})
		frappe.db.delete("Periskope Contact", {"phone": NUMBER})
		self.n = 0

	def _send(self, chat_id, text, phone, origin, detail=None):
		self.sent.append((chat_id, text, origin))
		return {"unique_id": f"3EB0{self.n}"}

	def _ai(self, prompt, user, schema, max_tokens):
		if prompt == welcome.WELCOME_PROMPT:
			self.ai["welcome_input"] = user
			greeting, _, request = self.ai["welcome"].partition(" May ")
			return {"greeting": greeting, "request": f"May {request}" if request else ""}
		return {key: "" for key in welcome.DETAILS} | self.ai["details"]

	def _incoming(self, body="Hi, need price of 30s cotton yarn", **kw):
		self.n += 1
		return messages.create_message(
			event(
				message_id=f"wel_{self.n}_{random.random()}",
				chat_id=CHAT,
				sender_phone=CHAT,
				org_phone=OURS,
				body=body,
				timestamp=_utc(),
				**kw,
			)
		)

	def _echo(self, text):
		self.n += 1
		return messages.create_message(
			event(
				message_id=f"true_{CHAT}_echo{self.n}",
				chat_id=CHAT,
				sender_phone=OURS,
				org_phone=OURS,
				from_me=True,
				body=text,
				timestamp=_utc(),
			)
		)

	def _welcome(self):
		return frappe.db.get_value(
			"Periskope Auto Reply",
			{"chat_id": CHAT, "kind": "Welcome"},
			["name", "status", "reply_text", "cancel_reason", "captured"],
			as_dict=True,
		)

	def _welcomed(self):
		"""The first message, and the queued welcome job run."""
		first = self._incoming()
		self.assertEqual(self.queued, [("send", {"message": first})])
		self.queued.clear()
		return welcome.send(first)


class TestWelcome(WelcomeCase):
	def test_a_new_number_is_welcomed_at_once_by_the_ai(self):
		self.assertEqual(self._welcomed(), "Welcome Sent")
		self.assertEqual(self.sent, [(CHAT, WELCOME, "Welcome")])
		row = self._welcome()
		self.assertEqual((row.status, row.reply_text), ("Welcome Sent", WELCOME))
		self.assertIn("30s cotton yarn", self.ai["welcome_input"])

	def test_messages_written_before_the_job_ran_are_read_too(self):
		first = self._incoming("Hi")
		self._incoming("I need dyed fabric")
		self.assertEqual(welcome.send(first), "Welcome Sent")
		self.assertIn("I need dyed fabric", self.ai["welcome_input"])

	def test_the_fixed_welcome_when_the_ai_cannot_be_asked_or_adds_numbers(self):
		with patch.object(welcome.assistant, "is_ready", return_value=False):
			self.assertEqual(welcome.compose("Hi"), welcome.DEFAULT_WELCOME)
		self.ai["welcome"] = "Hi! Our price is 245 per kg. Your name please?"
		self.assertEqual(welcome.compose("Hi, price?"), welcome.DEFAULT_WELCOME)

	def test_only_the_first_message_of_an_unknown_number(self):
		self._welcomed()
		self._incoming("one more thing")
		self.assertNotIn("send", [m for m, _ in self.queued])
		self.assertEqual(frappe.db.count("Periskope Auto Reply", {"chat_id": CHAT, "kind": "Welcome"}), 1)

	def test_no_welcome_when_switched_off_or_auto_reply_is_off(self):
		for values in ({"welcome_new_numbers": 0}, {"enable_auto_reply": 0}):
			settings(**{"enable_auto_reply": 1, "welcome_new_numbers": 1, **values})
			frappe.db.delete("Periskope Message", {"contact_number": NUMBER})
			self.queued.clear()
			self._incoming()
			self.assertEqual(self.queued, [], values)

	def test_no_welcome_for_a_number_on_a_lead_or_an_ignored_one(self):
		with patch.object(welcome, "by_mobile", return_value=[("CRM Lead", "x")]):
			self._incoming()
		frappe.db.delete("Periskope Message", {"contact_number": NUMBER})
		with patch.object(welcome, "is_ignored", return_value=True):
			self._incoming()
		self.assertEqual(self.queued, [])

	def test_no_welcome_after_a_rep_wrote_first(self):
		self._echo("Hello from Pranera")
		self._incoming()
		self.assertEqual(self.queued, [])

	def test_the_welcome_is_not_an_answer(self):
		self._welcomed()
		self._echo(WELCOME)
		echo = frappe.get_all(
			"Periskope Message", {"chat_id": CHAT, "direction": "Outgoing"}, pluck="agent_reply"
		)
		self.assertEqual(echo, ["Welcome"])
		# The customer's question still waits for a rep, and the auto reply is not cancelled.
		rows = frappe.get_all(
			"Periskope Message",
			{"chat_id": CHAT},
			[
				"direction",
				"message_timestamp",
				"agent_reply",
				"body",
				"message_type",
				"is_deleted",
				"needs_reply",
			],
			order_by="message_timestamp asc",
		)
		self.assertEqual(len(reminders.unanswered(rows)), 1)
		self.assertFalse(frappe.db.exists("Periskope Auto Reply", {"chat_id": CHAT, "status": "Cancelled"}))

	def test_test_mode_keeps_the_welcome_on_the_row(self):
		welcome.send_message.side_effect = safety.SendNotAllowed(safety.DRY_RUN)
		self.assertEqual(self._welcomed(), safety.DRY_RUN)
		row = self._welcome()
		self.assertEqual((row.status, row.cancel_reason), (safety.DRY_RUN, "Welcome message"))


class TestDetails(WelcomeCase):
	def setUp(self):
		super().setUp()
		self._welcomed()
		self.ai["details"] = {
			"name": "Ravi Kumar",
			"company": "Sri Ganesh Mills",
			"city_address": "Tiruppur",
			"email": "ravi@ganeshmills.example.com",
		}

	def _lead(self):
		return frappe.db.get_value(
			"CRM Lead",
			{"mobile_no": f"+{NUMBER}"},
			["name", "first_name", "last_name", "email", "organization"],
			as_dict=True,
		)

	def test_the_customers_reply_creates_and_fills_the_lead(self):
		self._incoming("Ravi Kumar, Sri Ganesh Mills, Tiruppur. ravi@ganeshmills.example.com")
		self.assertIn(("capture", {"chat_id": CHAT}), self.queued)
		written = welcome.capture(CHAT)
		self.assertEqual(set(written), set(welcome.DETAILS))
		lead = self._lead()
		self.assertEqual(
			(lead.first_name, lead.last_name, lead.email, lead.organization),
			("Ravi", "Kumar", "ravi@ganeshmills.example.com", "Sri Ganesh Mills"),
		)
		note = frappe.db.get_value(
			"FCRM Note", {"reference_docname": lead.name, "title": "Address"}, "content"
		)
		self.assertIn("Tiruppur", note)
		self.assertEqual(frappe.parse_json(self._welcome().captured)["email"], "ravi@ganeshmills.example.com")
		# Everything is known: later messages are no longer read.
		self.queued.clear()
		self._incoming("when can you deliver?")
		self.assertNotIn("capture", [m for m, _ in self.queued])

	def test_what_a_person_entered_is_never_replaced(self):
		self.ai["details"] = {"name": "Ravi Kumar"}
		self._incoming("Ravi Kumar here")
		welcome.capture(CHAT)
		lead = frappe.get_doc("CRM Lead", self._lead().name)
		lead.update({"first_name": "Ravichandran", "last_name": "", "email": "sales@ganesh.example.com"})
		lead.save(ignore_permissions=True)
		self.ai["details"] = {"name": "Ravi K", "email": "ravi@ganeshmills.example.com", "company": "Ganesh"}
		self._incoming("my mail ravi@ganeshmills.example.com")
		written = welcome.capture(CHAT)
		self.assertEqual(written, {"company": "Ganesh"})
		lead = self._lead()
		self.assertEqual((lead.first_name, lead.email), ("Ravichandran", "sales@ganesh.example.com"))

	def test_a_name_given_before_the_welcome_counts(self):
		frappe.db.set_value(
			"Periskope Message", {"chat_id": CHAT, "direction": "Incoming"}, "body", "Hi, I am Ravi Kumar"
		)
		self.ai["details"] = {"name": "Ravi Kumar"}
		self._incoming("Sri Ganesh Mills")
		seen = []
		with patch.object(
			welcome, "extract", side_effect=lambda w, text: seen.append(text) or {"name": "Ravi Kumar"}
		):
			welcome.capture(CHAT)
		self.assertIn("I am Ravi Kumar", seen[0])
		self.assertEqual(self._lead().first_name, "Ravi")

	def test_a_bad_email_is_left_out(self):
		self.ai["details"] = {"name": "Ravi", "email": "ravi at mills"}
		self._incoming("Ravi, ravi at mills")
		self.assertNotIn("email", welcome.capture(CHAT))
		self.assertFalse(self._lead().email)

	def test_in_test_mode_nothing_is_written_and_the_next_message_tries_again(self):
		settings(test_mode=1, test_allowed_chats="910000000000")
		self._incoming("Ravi Kumar, Sri Ganesh Mills")
		lead = self._lead()
		written = welcome.capture(CHAT)
		self.assertNotIn("email", written)
		self.assertNotIn("company", written)
		self.assertFalse(frappe.parse_json(self._welcome().captured or "{}").get("email"))
		self.assertTrue(lead is None or not lead.email)
