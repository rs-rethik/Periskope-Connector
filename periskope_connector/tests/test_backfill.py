"""Back-fill: messages a missed webhook never delivered are recovered from the chat history."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import backfill

CHAT = "919000000001@c.us"
PHONE = "919786387999"


def _iso(hours_ago):
	return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _msg(message_id, hours_ago, body="hi", from_me=False):
	return {
		"message_id": message_id,
		"chat_id": CHAT,
		"timestamp": _iso(hours_ago),
		"body": body,
		"from_me": from_me,
		"message_type": "chat",
		"org_phone": f"{PHONE}@c.us",
		"sender_phone": f"{PHONE}@c.us" if from_me else CHAT,
	}


def _chat(hours_ago, chat_id=CHAT):
	return {"chat_id": chat_id, "latest_message": {"timestamp": _iso(hours_ago)}}


def _fake_api(chats, messages):
	"""A stand-in for client.request that serves a chat list and one chat's messages (newest first)."""

	def fake(method, path, phone=None, json=None):
		if path.startswith("chats?"):
			return {"chats": chats}
		assert path.startswith(f"chats/{CHAT}/messages")
		return {"messages": sorted(messages, key=lambda m: m["timestamp"], reverse=True)}

	return fake


class TestBackfill(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never call Periskope."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch.object(backfill, "_linked_phones", lambda: [PHONE]),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: "Test Customer"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_backfill_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_backfill_test")
		frappe.db.set_single_value("Periskope Settings", "enabled", 1)

	def _run(self, chats, messages, days=1):
		with patch.object(backfill, "request", _fake_api(chats, messages)):
			return backfill.run(days=days)

	def _saved(self, message_id):
		return frappe.db.exists("Periskope Message", {"message_id": message_id})

	def test_a_missing_message_is_saved(self):
		result = self._run([_chat(2)], [_msg("bf_missing", 2, body="Hi, this is Rethik")])
		self.assertEqual(result["recovered"], 1)
		self.assertTrue(self._saved("bf_missing"))
		self.assertEqual(
			frappe.db.get_value("Periskope Message", {"message_id": "bf_missing"}, "body"),
			"Hi, this is Rethik",
		)

	def test_a_message_already_saved_is_not_saved_again(self):
		messages = [_msg("bf_known", 3)]
		self.assertEqual(self._run([_chat(3)], messages)["recovered"], 1)
		self.assertEqual(self._run([_chat(3)], messages)["recovered"], 0)
		self.assertEqual(frappe.db.count("Periskope Message", {"message_id": "bf_known"}), 1)

	def test_only_the_gap_is_filled_when_a_later_message_is_already_saved(self):
		# The real case: "Hello" arrived by webhook, the "Hi" before it did not.
		self._run([_chat(1)], [_msg("bf_later", 1)])
		result = self._run([_chat(1)], [_msg("bf_gap", 5), _msg("bf_later", 1)])
		self.assertEqual(result["recovered"], 1)
		self.assertTrue(self._saved("bf_gap"))

	def test_messages_older_than_the_lookback_are_left_alone(self):
		result = self._run([_chat(2)], [_msg("bf_old", 30), _msg("bf_new", 2)], days=1)
		self.assertEqual(result["recovered"], 1)
		self.assertFalse(self._saved("bf_old"))

	def test_chats_without_recent_activity_are_not_read(self):
		calls = []

		def fake(method, path, phone=None, json=None):
			calls.append(path)
			return {"chats": [_chat(100)]}

		with patch.object(backfill, "request", fake):
			result = backfill.run(days=1)
		self.assertEqual(result, {"recovered": 0, "chats": 0})
		self.assertTrue(all(path.startswith("chats?") for path in calls))

	def test_messages_are_saved_oldest_first(self):
		order = []
		messages = [_msg("bf_b", 1), _msg("bf_a", 3), _msg("bf_c", 0.5)]
		with patch.object(
			backfill, "create_message", lambda payload: order.append(payload["data"]["message_id"]) or "x"
		):
			self._run([_chat(1)], messages)
		self.assertEqual(order, ["bf_a", "bf_b", "bf_c"])

	def test_a_reply_marks_the_earlier_customer_message_as_replied(self):
		self._run([_chat(2)], [_msg("bf_ask", 3), _msg("bf_answer", 2, from_me=True)])
		self.assertEqual(frappe.db.get_value("Periskope Message", {"message_id": "bf_ask"}, "replied"), 1)

	def test_nothing_happens_when_the_integration_is_off(self):
		frappe.db.set_single_value("Periskope Settings", "enabled", 0)
		with patch.object(backfill, "request", side_effect=AssertionError("must not call Periskope")):
			self.assertEqual(backfill.run(), {"recovered": 0, "chats": 0})

	def test_one_failing_chat_does_not_stop_the_others(self):
		chats = [_chat(1, "919000000002@c.us"), _chat(1)]
		good = _msg("bf_good", 1)

		def fake(method, path, phone=None, json=None):
			if path.startswith("chats?"):
				return {"chats": chats}
			if "919000000002" in path:
				raise frappe.ValidationError("Periskope returned HTTP 500")
			return {"messages": [good]}

		# The job rolls back the failed chat; here that would also undo the test's own savepoint.
		with (
			patch.object(backfill, "request", fake),
			patch.object(frappe, "log_error") as logged,
			patch.object(frappe.db, "rollback"),
		):
			result = backfill.run(days=1)
		self.assertEqual(result["recovered"], 1)
		self.assertTrue(self._saved("bf_good"))
		logged.assert_called_once()
