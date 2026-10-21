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

	def test_a_failing_follow_up_does_not_undo_the_sent_holding_message(self):
		# For example a site's ToDo server script that throws while server scripts are disabled.
		self._incoming("Can you make a special sample in neon?")
		self._make_due()
		result = {"can_answer": False, "answer": "", "confidence": 0, "used_sources": [], "sources": []}
		with (
			patch.object(answer, "build", return_value=result),
			patch.object(reminders, "_create_todo", side_effect=frappe.ValidationError("server script")),
		):
			self.assertEqual(autoreply.run_due(), 1)
		row = self._row()
		self.assertEqual(row.status, "Holding Sent")
		self.assertEqual(row.reply_text, HOLDING)
		self.assertFalse(row.follow_up)
		self.send.assert_called_once()

	def test_the_echo_that_arrives_during_the_send_is_our_own_reply(self):
		# WhatsApp's copy of our message can come through the webhook while the send is still being
		# handled: the row must already be committed, or the webhook takes the copy for a rep's reply.
		self._incoming("Sample iruka?")
		self._make_due()
		echoes, committed = [], []
		# Commits are no-ops in tests (isolate): note what the row held at each one instead.
		frappe.db.commit.side_effect = lambda: committed.append(
			frappe.db.get_value("Periskope Auto Reply", self._row().name, ["status", "reply_text"])
		)

		def send(chat_id, text, *a, **k):
			# The webhook runs in its own transaction: it sees only what was committed before the send.
			self.assertEqual(committed[-1], ("Holding Sent", text))
			self.n += 1
			echoes.append(
				messages.create_message(
					event(
						message_id=f"echo_{self.n}",
						chat_id=CHAT,
						from_me=True,
						body=text,
						sender_phone="919786387999@c.us",
						timestamp=_utc(),
					)
				)
			)

		self.send.side_effect = send
		held = {"can_answer": False, "answer": "", "confidence": 0, "used_sources": [], "sources": []}
		with patch.object(answer, "build", return_value=held):
			autoreply.run_due()
		self.assertEqual(self._row().status, "Holding Sent")
		self.assertEqual(frappe.db.get_value("Periskope Message", echoes[0], "agent_reply"), "Holding")

	def test_the_holding_reply_is_the_ais_words_for_the_situation(self):
		self._incoming("The fabric is rejected, I will send a legal notice")
		self._make_due()
		sorry = "Sorry for the inconvenience. We are checking it from our side and our sales representative will contact you shortly. Please bear with us."
		result = {
			"can_answer": False,
			"answer": "",
			"confidence": 0,
			"used_sources": [],
			"sources": [],
			"holding": sorry,
		}
		with patch.object(answer, "build", return_value=result):
			autoreply.run_due()
		row = self._row()
		self.assertEqual((row.status, row.reply_text), ("Holding Sent", sorry))
		self.assertEqual(self.send.call_args.args[1], sorry)

	def test_after_a_holding_reply_a_new_question_is_answered_on_its_own(self):
		self._incoming("The fabric is rejected, I will send a legal notice")
		self._make_due()
		held = {"can_answer": False, "answer": "", "confidence": 0, "used_sources": [], "sources": []}
		with patch.object(answer, "build", return_value=held):
			autoreply.run_due()
		first = self._row().name
		self._incoming("Is MARS 200 WOC available?", minutes_ago=-0.1)
		frappe.db.set_value(
			"Periskope Auto Reply",
			{"chat_id": CHAT, "status": "Waiting"},
			"due_on",
			now_datetime() - timedelta(minutes=1),
		)
		asked = []
		result = {
			"can_answer": True,
			"answer": "Yes, MARS 200 WOC is in stock.",
			"confidence": 90,
			"used_sources": ["ITEM:x"],
			"sources": [],
		}
		with patch.object(answer, "build", side_effect=lambda text, *a, **k: asked.append(text) or result):
			autoreply.run_due()
		self.assertEqual(asked, ["Is MARS 200 WOC available?"])  # not held back by the complaint
		row = frappe.db.get_value(
			"Periskope Auto Reply",
			{"chat_id": CHAT, "name": ["!=", first]},
			["status", "reply_text"],
			as_dict=True,
		)
		self.assertEqual((row.status, row.reply_text), ("Sent", "Yes, MARS 200 WOC is in stock."))

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
		self._incoming("Hello? any update", minutes_ago=-0.1)
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

	def test_a_busy_chat_is_still_answered(self):
		# No daily cap: the AI answers every question that comes in.
		for _i in range(10):
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
		result = {
			"can_answer": True,
			"answer": "Yes, it is in stock.",
			"confidence": 90,
			"used_sources": ["KB:x"],
			"sources": [],
		}
		with patch.object(answer, "build", return_value=result):
			autoreply.run_due()
		self.send.assert_called_once()


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

	def test_with_no_data_the_ai_only_writes_the_holding_reply(self):
		reply = {
			"can_answer": True,
			"answer": "It ships in 3 days.",
			"confidence": 90,
			"used_sources": [],
			"holding": "Sorry for the trouble, our sales representative will contact you shortly.",
		}
		with (
			patch.object(answer, "gather", return_value=[]),
			patch.object(answer.assistant, "complete_json", return_value=reply) as call,
		):
			out = answer.build("My order is late, this is unacceptable")
		self.assertIn("DATA:\n(none)", call.call_args.args[1])
		self.assertFalse(out["can_answer"])  # nothing to answer from
		self.assertEqual(out["holding"], reply["holding"])

	def test_a_holding_reply_with_a_made_up_number_is_dropped(self):
		reply = {
			"can_answer": False,
			"answer": "",
			"confidence": 0,
			"used_sources": [],
			"holding": "Sorry, we will replace it within 2 days.",
		}
		with (
			patch.object(answer, "gather", return_value=[]),
			patch.object(answer.assistant, "complete_json", return_value=reply),
		):
			self.assertEqual(answer.build("The fabric is rejected")["holding"], "")

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
