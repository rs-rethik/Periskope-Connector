"""Tests for the Periskope Auto Reply doctype: the delay, the cancel rules, and what is sent."""

import random
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import answer, autoreply, knowledge, messages, reminders, safety
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

CHAT = "919600000777@c.us"
HOLDING = autoreply.DEFAULT_HOLDING


def _utc(minutes: float = 0) -> str:
	"""A webhook timestamp (UTC) that many minutes from now."""
	return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestAutoReply(FrappeTestCase):
	def setUp(self):
		self.sent_mail = isolate(self, "periskope_autoreply_test")
		patcher = patch.object(messages, "_rep_for_phone", lambda n: "Administrator")
		patcher.start()
		self.addCleanup(patcher.stop)
		self.sends = patch.object(autoreply, "send_message")
		self.send = self.sends.start()
		self.addCleanup(self.sends.stop)
		# The AI counts as usable whether or not the site has a key; answer.build is mocked per test.
		ready = patch.object(autoreply.assistant, "readiness", return_value=(True, ""))
		ready.start()
		self.addCleanup(ready.stop)
		settings(enabled=1, enable_auto_reply=1, reply_delay_minutes=3)
		# Only this test's replies may fall due: the site's own waiting ones are put aside (rolled back).
		frappe.db.delete("Periskope Auto Reply", {"status": "Waiting"})
		frappe.db.delete("Periskope Auto Reply", {"chat_id": CHAT})
		self.n = 0

	def _incoming(self, body="What is the price of dyed cotton?", **kw):
		self.n += 1
		stamp = _utc(-kw.pop("minutes_ago", 0))
		payload = event(
			message_id=f"ar_{self.n}_{random.random()}",
			chat_id=CHAT,
			sender_phone=CHAT,
			body=body,
			timestamp=stamp,
			**kw,
		)
		return messages.create_message(payload)

	def _row(self):
		return frappe.db.get_value(
			"Periskope Auto Reply",
			{"chat_id": CHAT},
			["name", "status", "due_on", "reply_text", "follow_up"],
			as_dict=True,
		)

	def _make_due(self):
		frappe.db.set_value(
			"Periskope Auto Reply", self._row().name, "due_on", now_datetime() - timedelta(minutes=1)
		)

	def test_nothing_is_scheduled_when_switched_off(self):
		settings(enable_auto_reply=0)
		self._incoming()
		self.assertIsNone(self._row())

	def test_customer_message_waits_for_the_delay(self):
		self._incoming()
		row = self._row()
		self.assertEqual(row.status, "Waiting")
		wait = (row.due_on - now_datetime()).total_seconds()
		self.assertTrue(170 <= wait <= 185, wait)
		self.assertEqual(autoreply.run_due(), 0)
		self.send.assert_not_called()

	def test_a_new_message_joins_the_waiting_reply_without_moving_it(self):
		# a customer who keeps writing is still answered in time
		self._incoming("Price of dyed cotton?")
		soon = now_datetime() + timedelta(seconds=10)
		frappe.db.set_value("Periskope Auto Reply", self._row().name, "due_on", soon)
		second = self._incoming("and the minimum quantity?")
		self.assertEqual(frappe.db.count("Periskope Auto Reply", {"chat_id": CHAT}), 1)
		row = frappe.get_doc("Periskope Auto Reply", self._row().name)
		self.assertLess((row.due_on - now_datetime()).total_seconds(), 15)
		self.assertEqual(row.last_customer_message, second)

	def test_the_wait_is_the_setting_with_a_default(self):
		self.assertEqual(autoreply.delay_for(SimpleNamespace(reply_delay_minutes=7)), timedelta(minutes=7))
		self.assertEqual(
			autoreply.delay_for(SimpleNamespace(reply_delay_minutes=None)),
			timedelta(minutes=autoreply.DEFAULT_DELAY_MINUTES),
		)

	def test_a_rep_reply_cancels(self):
		self._incoming()
		self.n += 1
		messages.create_message(
			event(
				message_id=f"rep_{self.n}",
				chat_id=CHAT,
				from_me=True,
				body="Hi, I will call you",
				sender_phone="919786387999@c.us",
			)
		)
		self.assertEqual(self._row().status, "Cancelled")

	def test_acknowledgement_does_not_schedule(self):
		self._incoming("ok thanks")
		self.assertIsNone(self._row())

	def test_old_recovered_message_is_not_answered(self):
		self._incoming(minutes_ago=300)
		self.assertIsNone(self._row())

	def test_group_chats_are_never_answered(self):
		self._incoming()
		frappe.db.delete("Periskope Auto Reply", {"chat_id": CHAT})
		self.n += 1
		messages.create_message(
			event(message_id=f"g_{self.n}", chat_id="1203639999@g.us", body="Price?", timestamp=_utc())
		)
		self.assertEqual(frappe.db.count("Periskope Auto Reply", {"chat_id": "1203639999@g.us"}), 0)

	def test_confident_answer_from_data_is_sent(self):
		self._incoming("What is the minimum order quantity?")
		self._make_due()
		result = {
			"can_answer": True,
			"answer": "The minimum order is 100 pieces.",
			"confidence": 92,
			"used_sources": ["KB:x"],
			"sources": [],
		}
		with patch.object(answer, "build", return_value=result):
			self.assertEqual(autoreply.run_due(), 1)
		row = self._row()
		self.assertEqual(row.status, "Sent")
		self.send.assert_called_once()
		self.assertEqual(self.send.call_args.args[1], "The minimum order is 100 pieces.")
		self.assertFalse(row.follow_up)

	def test_not_confident_sends_the_holding_message_and_follows_up(self):
		self._incoming("Can you make a special sample in neon?")
		self._make_due()
		result = {
			"can_answer": True,
			"answer": "Maybe",
			"confidence": 40,
			"used_sources": ["KB:x"],
			"sources": [],
		}
		with patch.object(answer, "build", return_value=result):
			autoreply.run_due()
		row = self._row()
		self.assertEqual(row.status, "Holding Sent")
		self.assertEqual(self.send.call_args.args[1], HOLDING)
		self.assertTrue(row.follow_up)
		self.assertTrue(frappe.db.exists("Periskope Reply Reminder", {"chat_id": CHAT, "status": "Open"}))
		self.assertTrue(
			frappe.db.exists("ToDo", {"allocated_to": "Administrator", "reference_type": "Periskope Message"})
		)

	def test_holding_message_is_not_taken_for_a_reps_reply(self):
		self._incoming("Special sample in neon?")
		self._make_due()
		with patch.object(
			answer,
			"build",
			return_value={
				"can_answer": False,
				"answer": "",
				"confidence": 0,
				"used_sources": [],
				"sources": [],
			},
		):
			autoreply.run_due()
		self.n += 1
		name = messages.create_message(
			event(
				message_id=f"own_{self.n}",
				chat_id=CHAT,
				from_me=True,
				body=HOLDING,
				sender_phone="919786387999@c.us",
				timestamp=_utc(),
			)
		)
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "agent_reply"), "Holding")
		# The customer is still waiting: the reminder engine must not count the holding message.
		rows = frappe.get_all(
			"Periskope Message",
			filters={"chat_id": CHAT},
			fields=[
				"name",
				"direction",
				"body",
				"message_type",
				"message_timestamp",
				"classification_status",
				"needs_reply",
				"agent_reply",
			],
		)
		self.assertTrue(reminders.unanswered(rows))
		# A second customer message does not trigger a second holding message.
		self._incoming("Hello? any update")
		self._make_due()
		self.send.reset_mock()
		with patch.object(
			answer,
			"build",
			return_value={
				"can_answer": False,
				"answer": "",
				"confidence": 0,
				"used_sources": [],
				"sources": [],
			},
		):
			autoreply.run_due()
		self.send.assert_not_called()
		self.assertEqual(self._row().status, "Skipped")

	def test_a_rep_who_wrote_while_the_answer_was_prepared_wins(self):
		self._incoming()
		self._make_due()

		def rep_writes(*a, **k):
			self.n += 1
			messages.create_message(
				event(
					message_id=f"late_{self.n}",
					chat_id=CHAT,
					from_me=True,
					body="On it",
					sender_phone="919786387999@c.us",
					timestamp=_utc(1),
				)
			)
			return {
				"can_answer": True,
				"answer": "Reply",
				"confidence": 99,
				"used_sources": ["KB:x"],
				"sources": [],
			}

		with patch.object(answer, "build", side_effect=rep_writes):
			autoreply.process(self._row().name)
		self.send.assert_not_called()

	def test_send_failure_is_recorded_and_the_rep_is_told(self):
		self._incoming()
		self._make_due()
		self.send.side_effect = frappe.ValidationError("Periskope returned HTTP 500")
		with patch.object(
			answer,
			"build",
			return_value={
				"can_answer": True,
				"answer": "Ok",
				"confidence": 95,
				"used_sources": ["KB:x"],
				"sources": [],
			},
		):
			autoreply.run_due()
		row = self._row()
		self.assertEqual(row.status, "Failed")
		self.assertTrue(row.follow_up)

	def test_test_mode_keeps_the_reply_but_sends_and_follows_up_nothing(self):
		self._incoming()
		self._make_due()
		self.send.side_effect = safety.SendNotAllowed(safety.DRY_RUN)
		with patch.object(
			answer,
			"build",
			return_value={
				"can_answer": True,
				"answer": "It is 120 per kg.",
				"confidence": 95,
				"used_sources": ["KB:x"],
				"sources": [],
			},
		):
			autoreply.run_due()
		row = self._row()
		self.assertEqual((row.status, row.reply_text), ("Dry Run", "It is 120 per kg."))
		self.assertFalse(row.follow_up)

	def test_daily_limit_per_chat(self):
		for _i in range(autoreply.MAX_PER_CHAT_PER_DAY):
			frappe.get_doc(
				{
					"doctype": "Periskope Auto Reply",
					"chat_id": CHAT,
					"status": "Sent",
					"sent_on": now_datetime(),
				}
			).insert()
		self._incoming()
		frappe.db.set_value(
			"Periskope Auto Reply",
			{"chat_id": CHAT, "status": "Waiting"},
			"due_on",
			now_datetime() - timedelta(minutes=1),
		)
		autoreply.run_due()
		self.send.assert_not_called()
		self.assertEqual(
			frappe.db.get_value("Periskope Auto Reply", {"chat_id": CHAT, "status": "Skipped"}, "name")
			is not None,
			True,
		)


class TestAnswerGuards(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_answer_test")

	def test_numbers_must_come_from_the_data(self):
		sources = [{"id": "KB:1", "title": "MOQ", "text": "The minimum order is 100 pieces."}]
		self.assertTrue(answer.grounded("Minimum is 100 pieces.", sources, "moq?"))
		self.assertFalse(answer.grounded("Minimum is 250 pieces.", sources, "moq?"))
		self.assertTrue(answer.grounded("For 50 kg the minimum is 100 pieces.", sources, "I need 50 kg"))

	def test_item_price_decimals_match_whole_numbers(self):
		# Item Price and Bin give 245.0 and 1,500.0; the AI writes 245 and 1500.
		sources = [{"id": "ITEM:Y", "title": "Yarn", "text": "in_stock: 1500.0, price: 245.0 INR per Kg"}]
		self.assertTrue(answer.grounded("It is 245 INR per Kg and we have 1,500 kg.", sources, "price?"))
		self.assertTrue(answer.grounded("It is 245.00 INR per Kg.", sources, "price?"))
		self.assertFalse(answer.grounded("It is 254 INR per Kg.", sources, "price?"))

	def test_no_data_means_no_ai_call(self):
		with (
			patch.object(answer, "gather", return_value=[]),
			patch.object(answer.assistant, "complete_json") as call,
		):
			out = answer.build("Tell me about quantum computing")
		call.assert_not_called()
		self.assertFalse(out["can_answer"])

	def test_answer_must_name_a_source_it_was_given(self):
		sources = [{"id": "KB:1", "title": "MOQ", "text": "100 pieces"}]
		reply = {"can_answer": True, "answer": "100 pieces.", "confidence": 90, "used_sources": ["KB:nope"]}
		with (
			patch.object(answer, "gather", return_value=sources),
			patch.object(answer.assistant, "complete_json", return_value=reply),
		):
			self.assertFalse(answer.build("moq")["can_answer"])
		reply["used_sources"] = ["KB:1"]
		with (
			patch.object(answer, "gather", return_value=sources),
			patch.object(answer.assistant, "complete_json", return_value=reply),
		):
			out = answer.build("moq")
		self.assertTrue(out["can_answer"])
		self.assertEqual(out["used_sources"], ["KB:1"])

	def test_gather_finds_knowledge_entries(self):
		entry = frappe.get_doc(
			{
				"doctype": "Periskope Knowledge Entry",
				"question": "What is the minimum order quantity?",
				"answer": "100 pieces.",
				"status": "Approved",
			}
		).insert()
		ids = [s["id"] for s in answer.gather("minimum order quantity?")]
		self.assertIn(f"KB:{entry.name}", ids)
		self.assertTrue(knowledge.find("minimum order quantity"))


class TestNoBudgetNoAI(FrappeTestCase):
	"""With no AI key or the budget used up: no AI call, the holding message, one email to the admin."""

	def setUp(self):
		self.mail = isolate(self, "periskope_budget_test")
		patcher = patch.object(messages, "_rep_for_phone", lambda n: "Administrator")
		patcher.start()
		self.addCleanup(patcher.stop)
		self.send = patch.object(autoreply, "send_message").start()
		self.addCleanup(patch.stopall)
		settings(enabled=1, enable_auto_reply=1, reply_delay_minutes=0, approver_emails="boss@example.com")
		frappe.cache().delete_value("periskope_alert:not_ready:budget")
		self.addCleanup(frappe.cache().delete_value, "periskope_alert:not_ready:budget")

	def test_holding_message_and_a_single_warning(self):
		from periskope_connector.ai import assistant

		with (
			patch.object(assistant, "readiness", return_value=(False, "budget")),
			patch.object(answer, "build") as build,
		):
			for n in range(2):
				messages.create_message(
					event(
						message_id=f"nb_{n}_{random.random()}",
						chat_id=CHAT,
						sender_phone=CHAT,
						body="Price?",
						timestamp=_utc(),
					)
				)
				frappe.db.set_value(
					"Periskope Auto Reply",
					{"chat_id": CHAT, "status": "Waiting"},
					"due_on",
					now_datetime() - timedelta(minutes=1),
				)
				autoreply.run_due()
		build.assert_not_called()
		self.assertEqual(self.send.call_args.args[1], HOLDING)
		warnings = [c for c in self.mail.call_args_list if "budget" in c.kwargs.get("subject", "")]
		self.assertEqual(len(warnings), 1)
