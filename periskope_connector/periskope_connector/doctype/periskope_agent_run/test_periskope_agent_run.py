"""Tests for the Periskope Agent Run doctype and the background learning agent."""

import json
from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import agent, knowledge, learner
from periskope_connector.ai import assistant
from periskope_connector.system import insert_as_system
from periskope_connector.tests.utils import isolate, settings

_counter = [0]


def _message(body, direction="Incoming", chat="919600000888@c.us", minutes_ago=180):
	_counter[0] += 1
	return insert_as_system(
		{
			"doctype": "Periskope Message",
			"message_id": f"agent_test_{_counter[0]}_{frappe.generate_hash(length=6)}",
			"direction": direction,
			"message_type": "text",
			"body": body,
			"chat_id": chat,
			"contact_number": "919600000888",
			"message_timestamp": now_datetime() - timedelta(minutes=minutes_ago),
		}
	)


class TestAgent(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_agent_test")
		frappe.db.delete("Periskope Agent Run")
		frappe.db.delete("Periskope Knowledge Entry")
		frappe.db.delete("Periskope Suggestion")
		# Start after the site's own messages: only what the tests create is read.
		since = now_datetime() - timedelta(minutes=200)
		frappe.db.delete("Periskope Message", {"message_timestamp": [">", since]})
		insert_as_system({"doctype": "Periskope Agent Run", "status": "Done", "cursor_to": since})
		frappe.cache().delete_value("periskope_agent_learn")
		frappe.cache().delete_value("periskope_agent_stale")
		settings(enable_agent=1, approver_emails="boss@example.com", suggestion_email_gap_hours=0)
		ready = patch.object(assistant, "is_ready", return_value=True)
		ready.start()
		self.addCleanup(ready.stop)

	def test_off_means_nothing_runs(self):
		settings(enable_agent=0)
		before = frappe.db.count("Periskope Agent Run")
		self.assertIn("skipped", agent.run())
		self.assertEqual(frappe.db.count("Periskope Agent Run"), before)

	def test_no_ai_key_skips_quietly(self):
		with patch.object(assistant, "is_ready", return_value=False):
			self.assertIn("skipped", agent.run())

	def test_pass_builds_knowledge_and_advances_the_cursor(self):
		_message("What is the minimum order quantity?")
		_message("It is 100 pieces per colour.", direction="Outgoing")
		reply = {
			"entries": [
				{
					"question": "What is the minimum order quantity?",
					"answer": "100 pieces per colour.",
					"keywords": "moq",
					"confidence": 90,
					"evidence_chat": 1,
				}
			]
		}
		with patch.object(assistant, "complete_json", return_value=reply) as call:
			out = agent.run()
		self.assertEqual(out["knowledge"], 1)
		self.assertEqual(call.call_count, 1)
		run = frappe.get_all(
			"Periskope Agent Run",
			filters={"knowledge_added": 1},
			fields=["status", "cursor_to", "knowledge_added"],
		)[0]
		self.assertEqual((run.status, run.knowledge_added), ("Done", 1))
		self.assertEqual(frappe.db.get_value("Periskope Knowledge Entry", {}, "status"), "Suggested")
		# The next pass starts where this one stopped and finds nothing new.
		with patch.object(assistant, "complete_json") as call:
			out = agent.run()
		call.assert_not_called()
		self.assertEqual(out["messages"], 0)

	def test_active_chats_wait_until_they_are_quiet(self):
		_message("Is the shade available?", minutes_ago=5)
		with patch.object(assistant, "complete_json") as call:
			agent.run()
		call.assert_not_called()

	def test_a_failing_pass_is_recorded_and_retried_later(self):
		_message("What is the minimum order quantity?")
		with patch.object(assistant, "complete_json", side_effect=frappe.ValidationError("boom")):
			with self.assertRaises(frappe.ValidationError):
				agent.run()
		self.assertTrue(frappe.db.exists("Periskope Agent Run", {"status": "Failed"}))
		self.assertIn("skipped", agent.run())  # paused for a while, no new AI spend

	def test_pass_emails_the_pending_suggestions(self):
		_message("What is the minimum order quantity?")
		reply = {
			"entries": [
				{
					"question": "What is the minimum order quantity?",
					"answer": "100 pieces.",
					"keywords": "",
					"confidence": 90,
					"evidence_chat": 1,
				}
			]
		}
		with (
			patch.object(assistant, "complete_json", return_value=reply),
			patch.object(frappe, "sendmail") as mail,
		):
			out = agent.run()
		self.assertEqual(out["emails"], 1)
		mail.assert_called_once()


class TestLearner(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_learner_test")
		frappe.db.delete("Periskope Suggestion")
		settings(learning_min_evidence=2)

	def _analyse(self, items, chats=None):
		chats = chats or {1: ["M1"], 2: ["M2"], 3: ["M3"]}
		with patch.object(assistant, "complete_json", return_value={"suggestions": items}):
			return learner.analyse("transcript", chats)

	def _item(self, **kw):
		base = {
			"kind": "Tag",
			"operation": "Add",
			"target": "",
			"summary": "Add a tag",
			"reason": "Seen often",
			"payload_json": json.dumps({"tag_name": "bulk_order", "description": "bulk"}),
			"evidence_chats": [1, 2],
		}
		base.update(kw)
		return base

	def test_a_well_backed_suggestion_is_saved(self):
		self.assertEqual(self._analyse([self._item()]), 1)
		doc = frappe.get_all("Periskope Suggestion", fields=["status", "kind", "evidence_messages"])[0]
		self.assertEqual((doc.status, doc.kind), ("Pending", "Tag"))
		self.assertIn("M1", doc.evidence_messages)

	def test_not_enough_evidence_is_dropped(self):
		self.assertEqual(self._analyse([self._item(evidence_chats=[1])]), 0)

	def test_evidence_must_exist(self):
		self.assertEqual(self._analyse([self._item(evidence_chats=[8, 9])]), 0)

	def test_existing_tag_cannot_be_added_again(self):
		name = frappe.get_all("Periskope Tag", pluck="name")[0]
		item = self._item(payload_json=json.dumps({"tag_name": name, "description": "x"}))
		self.assertEqual(self._analyse([item]), 0)

	def test_modify_of_an_unknown_record_is_dropped(self):
		self.assertEqual(self._analyse([self._item(operation="Modify", target="no_such_tag")]), 0)

	def test_secrets_and_switches_cannot_be_suggested(self):
		for field in ("anthropic_api_key", "enable_auto_reply", "alert_emails", "webhook_signing_secret"):
			item = self._item(
				kind="Setting", operation="Modify", target=field, payload_json=json.dumps({"value": "x"})
			)
			self.assertEqual(self._analyse([item]), 0, field)

	def test_allowed_setting_change_is_saved_with_the_current_value(self):
		item = self._item(
			kind="Setting",
			operation="Modify",
			target="reply_delay_minutes",
			payload_json=json.dumps({"value": 5}),
		)
		self.assertEqual(self._analyse([item]), 1)
		current = json.loads(
			frappe.get_all("Periskope Suggestion", fields=["current_value"])[0].current_value
		)
		self.assertIn("value", current)

	def test_not_more_than_the_cap_per_run(self):
		items = [
			self._item(payload_json=json.dumps({"tag_name": f"topic_{i}", "description": "d"}))
			for i in range(12)
		]
		self.assertEqual(self._analyse(items), learner.MAX_SUGGESTIONS)
