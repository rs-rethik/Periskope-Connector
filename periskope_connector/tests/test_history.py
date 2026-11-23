from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import backfill, history
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

PHONE = "919786387999"
OLD_CHAT = "919600088801@c.us"  # a customer who wrote months ago and got no answer
NEW_CHAT = "919600088802@c.us"  # a customer who wrote an hour ago


def _iso(delta: timedelta) -> str:
	return (datetime.now(timezone.utc) - delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _message(message_id, chat_id, age, from_me=False):
	return {
		"message_id": message_id,
		"chat_id": chat_id,
		"from_me": from_me,
		"org_phone": f"{PHONE}@c.us",
		"sender_phone": f"{PHONE}@c.us" if from_me else chat_id,
		"sender_name": "Zed Customer",
		"message_type": "chat",
		"body": f"message {message_id}",
		"timestamp": _iso(age),
	}


# Periskope answers newest first, as its API does.
MESSAGES = {
	OLD_CHAT: [
		_message("hist_old_2", OLD_CHAT, timedelta(days=100)),
		_message("hist_old_1", OLD_CHAT, timedelta(days=120)),
		_message("hist_too_old", OLD_CHAT, timedelta(days=250)),  # before the 6 months
	],
	NEW_CHAT: [_message("hist_new_1", NEW_CHAT, timedelta(hours=1))],
}
CHATS = [
	{"chat_id": OLD_CHAT, "latest_message": {"timestamp": _iso(timedelta(days=100))}},
	{"chat_id": NEW_CHAT, "latest_message": {"timestamp": _iso(timedelta(hours=1))}},
	{"chat_id": "919600088803@c.us", "latest_message": {"timestamp": _iso(timedelta(days=300))}},
]


def fake_periskope(method, path, phone=None, **kwargs):
	if path.startswith("chats?"):
		return {"chats": CHATS if "offset=0" in path else []}
	chat_id = path.split("/")[1]
	return {"messages": MESSAGES.get(chat_id, []) if path.endswith("offset=0") else []}


class TestHistoryImport(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_history_test")
		settings(enabled=1)

	def run_import(self):
		with (
			patch.object(backfill, "_linked_phones", return_value=[PHONE]),
			patch.object(backfill, "request", side_effect=fake_periskope),
		):
			return history.import_history()

	def chat(self, chat_id):
		return frappe.db.get_value(
			"Periskope Chat", {"chat_id": chat_id}, ["status", "last_message_preview"], as_dict=True
		)

	def test_saves_the_last_six_months_only(self):
		self.assertEqual(self.run_import(), 3)
		saved = frappe.get_all(
			"Periskope Message", filters={"message_id": ["like", "hist_%"]}, pluck="message_id"
		)
		self.assertEqual(sorted(saved), ["hist_new_1", "hist_old_1", "hist_old_2"])
		self.assertFalse(frappe.db.exists("Periskope Chat", {"chat_id": "919600088803@c.us"}))

	def test_no_ai_runs_on_imported_messages(self):
		self.run_import()
		statuses = frappe.get_all(
			"Periskope Message", filters={"message_id": ["like", "hist_%"]}, pluck="classification_status"
		)
		self.assertTrue(statuses)
		self.assertFalse([s for s in statuses if s])
		self.assertFalse(frappe.flags.periskope_history)

	def test_a_second_run_adds_nothing(self):
		self.run_import()
		self.assertEqual(self.run_import(), 0)

	def test_old_chats_are_resolved_and_a_recent_one_stays_open(self):
		self.run_import()
		self.assertEqual(self.chat(OLD_CHAT).status, "Resolved")
		self.assertEqual(self.chat(OLD_CHAT).last_message_preview, "message hist_old_2")
		self.assertEqual(self.chat(NEW_CHAT).status, "Open")

	def test_one_bad_message_does_not_block_the_rest(self):
		saved = []

		def create(payload):
			if payload["data"]["message_id"] == "bad":
				raise ValueError("malformed")
			saved.append(payload["data"]["message_id"])
			return "ok"

		with (
			patch.object(backfill, "_linked_phones", return_value=[PHONE]),
			patch.object(backfill, "_active_chats", return_value=["c1@c.us"]),
			patch.object(
				backfill, "_missing_messages", return_value=[{"message_id": m} for m in ("a", "bad", "b")]
			),
			patch.object(history, "create_message", side_effect=create),
			patch.object(frappe, "log_error"),
		):
			self.assertEqual(history.import_history(), 2)
		self.assertEqual(saved, ["a", "b"])
		self.assertFalse(frappe.flags.periskope_history)

	def test_nothing_runs_when_periskope_is_off(self):
		settings(enabled=0)
		with patch.object(backfill, "request") as request:
			self.assertEqual(history.import_history(), 0)
		request.assert_not_called()
		self.assertIn(
			"not enabled", frappe.db.get_single_value("Periskope Settings", "periskope_import_status")
		)
