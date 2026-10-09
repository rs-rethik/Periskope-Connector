from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import messages, reminders

# A Tuesday far in the future, so the site's real messages are outside the look-back window.
TUE = datetime(2031, 9, 1)
while TUE.weekday() != 1:
	TUE += timedelta(days=1)


def at(day_offset: int, hour: int, minute: int = 0) -> datetime:
	return TUE + timedelta(days=day_offset, hours=hour, minutes=minute)


def cfg(**kw):
	base = {
		"enabled": True,
		"start": datetime(2000, 1, 1, 9, 0).time(),
		"end": datetime(2000, 1, 1, 19, 0).time(),
		"days": reminders.WEEKDAYS["Monday to Saturday"],
		"holidays": set(),
		"l1": 1.0,
		"l2": 3.0,
		"l3": 8.0,
		"high_minutes": 30.0,
		"emails": ["boss@example.com"],
		"md_emails": ["md@example.com"],
		"groups": False,
		"ack_words": [],
	}
	base.update(kw)
	if "levels" not in base:
		base["levels"] = reminders.default_levels(base["l1"], base["l2"], base["l3"], base["high_minutes"])
	return SimpleNamespace(**base)


def msg(body="", message_type="text", needs_reply=0, status="", severity=None):
	return frappe._dict(
		body=body,
		message_type=message_type,
		needs_reply=needs_reply,
		classification_status=status,
		severity=severity,
	)


class TestWorkingHours(FrappeTestCase):
	def test_night_message_counts_from_next_morning(self):
		self.assertEqual(reminders.working_seconds(at(0, 20), at(1, 10), cfg()) / 3600, 1.0)

	def test_sunday_is_skipped(self):
		saturday = at(4, 18, 30)
		monday = at(6, 9, 30)
		self.assertEqual(saturday.weekday(), 5)
		self.assertEqual(reminders.working_seconds(saturday, monday, cfg()) / 3600, 1.0)

	def test_holidays_do_not_count_as_waiting_time(self):
		# Diwali shutdown: Wednesday is a working day, but not that Wednesday.
		c = cfg(holidays={at(2, 0).date()})
		self.assertEqual(reminders.working_seconds(at(1, 18), at(3, 10), c) / 3600, 2.0)
		self.assertFalse(reminders.is_working_time(at(2, 12), c))

	def test_full_working_day_is_ten_hours(self):
		self.assertEqual(reminders.working_seconds(at(0, 0), at(1, 0), cfg()) / 3600, 10.0)

	def test_is_working_time(self):
		c = cfg()
		self.assertFalse(reminders.is_working_time(at(0, 8, 59), c))
		self.assertTrue(reminders.is_working_time(at(0, 9), c))
		self.assertFalse(reminders.is_working_time(at(0, 19), c))
		self.assertFalse(reminders.is_working_time(at(5, 12), c))  # Sunday


class TestNeedsReply(FrappeTestCase):
	def test_acknowledgements_emoji_and_tiny_texts_need_no_reply(self):
		for body in ["ok", "OK.", "k", "Thanks!", "thank you", "👍", "🙏🙏", "😀", "hm", "noted"]:
			self.assertFalse(reminders.needs_reply(msg(body)), body)

	def test_the_sites_own_ack_words_are_respected(self):
		self.assertTrue(reminders.needs_reply(msg("sari")))
		self.assertFalse(reminders.needs_reply(msg("sari"), extra_words=["sari", "seri"]))
		self.assertFalse(reminders.needs_reply(msg("ok sari"), extra_words=["sari", "seri"]))
		self.assertFalse(reminders.needs_reply(msg("seri sir"), extra_words=["sari", "seri", "sir"]))

	def test_real_questions_need_a_reply_even_when_short(self):
		for body in ["Price?", "Available?", "Please send the quote", "Hello", "Call me"]:
			self.assertTrue(reminders.needs_reply(msg(body)), body)

	def test_attachment_without_text_needs_a_reply(self):
		self.assertTrue(reminders.needs_reply(msg("", message_type="document")))
		self.assertFalse(reminders.needs_reply(msg("", message_type="text")))

	def test_ai_decision_is_respected_once_tagged(self):
		self.assertFalse(reminders.needs_reply(msg("We will pay on Friday", status="Done", needs_reply=0)))
		self.assertTrue(reminders.needs_reply(msg("Send the invoice", status="Done", needs_reply=1)))


class TestRun(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for p in (
			patch.object(frappe.db, "commit"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			p.start()
			self.addCleanup(p.stop)
		self.mail = patch.object(frappe, "sendmail")
		self.sendmail = self.mail.start()
		self.addCleanup(self.mail.stop)
		frappe.db.savepoint("periskope_reminder_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_reminder_test")

		self.config = cfg()
		p = patch.object(reminders, "get_config", lambda *a, **k: self.config)
		p.start()
		self.addCleanup(p.stop)
		self.seq = 0

	def add(
		self,
		when,
		incoming=True,
		body="Please send the price list",
		chat="919600000001@c.us",
		severity=None,
	):
		self.seq += 1
		name = messages.create_message(
			{
				"event_type": "message.created",
				"data": {
					"message_id": f"t_{self.seq}",
					"chat_id": chat,
					"from_me": not incoming,
					"org_phone": "919786387999@c.us",
					"message_type": "chat",
					"body": body,
					"timestamp": "2031-01-01T00:00:00Z",
				},
			}
		)
		frappe.db.set_value("Periskope Message", name, "message_timestamp", when)
		if severity:
			frappe.db.set_value("Periskope Message", name, "severity", severity)
		return name

	def reminders(self, chat="919600000001@c.us"):
		return frappe.get_all(
			"Periskope Reply Reminder",
			{"chat_id": chat},
			["name", "status", "level", "todo", "first_response_secs"],
		)

	def test_l1_notifies_the_rep_without_a_todo(self):
		self.add(at(0, 9))
		stats = reminders.run(at(0, 10, 30))
		self.assertEqual((stats["notified"], stats["escalated"]), (1, 0))
		rows = self.reminders()
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].status, rows[0].level), ("Open", 1))
		self.assertFalse(rows[0].todo)
		self.assertEqual(reminders.run(at(0, 10, 40))["notified"], 0)  # no duplicate bell

	def test_nothing_happens_before_the_wait_is_long_enough(self):
		self.add(at(0, 9))
		self.assertEqual(reminders.run(at(0, 9, 50))["notified"], 0)

	def test_l2_creates_one_todo_and_emails_the_managers(self):
		self.add(at(0, 9))
		reminders.run(at(0, 10, 5))  # L1
		self.assertEqual(reminders.run(at(0, 11, 30))["escalated"], 0)  # only 2.6h since waiting
		stats = reminders.run(at(0, 12, 10))  # 3.2 working hours: L2
		self.assertEqual((stats["escalated"], stats["md_emailed"]), (1, 0))
		row = self.reminders()[0]
		self.assertEqual((row.status, row.level), ("Escalated", 2))
		self.assertEqual(frappe.db.get_value("ToDo", row.todo, "status"), "Open")
		self.assertEqual(frappe.db.get_value("ToDo", row.todo, "allocated_to"), "Administrator")
		self.assertEqual(reminders.run(at(0, 13))["escalated"], 0)  # no second L2
		self.assertEqual(self.sendmail.call_count, 1)
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], ["boss@example.com"])

	def test_l3_emails_the_escalation_list_once(self):
		self.add(at(0, 9))
		reminders.run(at(0, 10, 5))  # L1
		reminders.run(at(0, 12, 10))  # L2
		self.assertEqual(reminders.run(at(0, 16))["md_emailed"], 0)  # 7h: not yet
		self.assertEqual(reminders.run(at(0, 17, 30))["md_emailed"], 1)  # 8.5h: L3
		self.assertEqual(reminders.run(at(0, 18))["md_emailed"], 0)  # no second L3
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], ["md@example.com"])
		self.assertEqual(self.reminders()[0].level, 3)

	def test_high_severity_takes_the_fast_path(self):
		self.add(at(0, 9), severity="High")
		stats = reminders.run(at(0, 9, 40))  # 40 minutes, not 3 hours
		self.assertEqual((stats["escalated"], stats["md_emailed"]), (1, 1))
		row = self.reminders()[0]
		self.assertEqual(row.level, 3)
		self.assertTrue(row.todo)  # the rep still gets the action item

	def test_a_low_severity_message_never_takes_the_fast_path(self):
		self.add(at(0, 9))
		stats = reminders.run(at(0, 9, 40))
		self.assertEqual((stats["notified"], stats["escalated"], stats["md_emailed"]), (0, 0, 0))

	def test_night_message_waits_for_working_hours(self):
		self.add(at(0, 20))
		self.assertEqual(reminders.run(at(0, 23))["skipped"], "outside working hours")
		self.assertEqual(reminders.run(at(1, 9, 30))["notified"], 0)  # only 30 working minutes
		self.assertEqual(reminders.run(at(1, 10, 5))["notified"], 1)

	def test_nothing_happens_on_sunday(self):
		self.add(at(4, 18))
		self.assertEqual(reminders.run(at(5, 12))["skipped"], "outside working hours")

	def test_nothing_happens_on_a_holiday(self):
		self.config = cfg(holidays={at(1, 0).date()})
		self.add(at(0, 18))
		self.assertEqual(reminders.run(at(1, 12))["skipped"], "outside working hours")

	def test_acknowledgements_do_not_create_reminders(self):
		self.add(at(0, 9), body="ok")
		self.add(at(0, 9, 1), body="👍")
		self.assertEqual(reminders.run(at(0, 15))["notified"], 0)

	def test_rep_reply_closes_reminder_todo_and_records_the_response_time(self):
		self.add(at(0, 9))
		reminders.run(at(0, 10, 5))  # L1
		reminders.run(at(0, 12, 10))  # L2
		todo = self.reminders()[0].todo
		self.add(at(0, 12, 40), incoming=False, body="Sending it now")  # 3.7 working hours later
		self.assertEqual(reminders.run(at(0, 13))["closed"], 1)
		row = self.reminders()[0]
		self.assertEqual(row.status, "Closed")
		self.assertEqual(frappe.db.get_value("ToDo", todo, "status"), "Closed")
		self.assertAlmostEqual(row.first_response_secs / 3600, 3 + 2 / 3, places=5)

	def test_a_new_question_after_a_reply_rearms_the_whole_ladder(self):
		self.add(at(0, 9))
		reminders.run(at(0, 10, 5))
		self.add(at(0, 10, 20), incoming=False, body="Here you go")
		reminders.run(at(0, 10, 30))
		self.add(at(0, 11), body="Can you reduce the price?")
		self.assertEqual(reminders.run(at(0, 12))["notified"], 1)  # a fresh L1 cycle
		self.assertEqual(reminders.run(at(0, 14, 30))["escalated"], 1)  # fresh L2 (3.5h)

	def test_different_chats_get_separate_reminders(self):
		self.add(at(0, 9), chat="919600000001@c.us")
		self.add(at(0, 9), chat="919600000002@c.us")
		self.assertEqual(reminders.run(at(0, 10, 30))["notified"], 2)

	def test_group_chats_only_when_enabled(self):
		self.add(at(0, 9), chat="1203630@g.us")
		self.assertEqual(reminders.run(at(0, 10, 30))["notified"], 0)
		self.config = cfg(groups=True)
		self.assertEqual(reminders.run(at(0, 10, 30))["notified"], 1)

	def test_any_rep_in_a_group_resolves_it(self):
		self.config = cfg(groups=True)
		self.add(at(0, 9), chat="1203630@g.us")
		reminders.run(at(0, 10, 30))
		self.add(at(0, 10, 40), incoming=False, body="On it", chat="1203630@g.us")
		self.assertEqual(reminders.run(at(0, 10, 50))["closed"], 1)

	def test_one_failing_chat_does_not_stop_the_others_or_leave_half_its_work(self):
		bad_chat, good_chat = "919600000001@c.us", "919600000002@c.us"
		self.add(at(0, 9), chat=bad_chat)
		self.add(at(0, 9), chat=good_chat)
		real = reminders.create_reminder

		def flaky(chat_id, *args, **kwargs):
			if chat_id == bad_chat:
				# save the bell notification, then fail before the reminder row: this half must be undone
				frappe.get_doc(
					{
						"doctype": "Notification Log",
						"type": "Alert",
						"for_user": "Administrator",
						"subject": "half done",
						"document_type": "Periskope Message",
					}
				).insert(ignore_permissions=True)
				raise RuntimeError("cannot save the reminder")
			return real(chat_id, *args, **kwargs)

		with (
			patch.object(reminders, "create_reminder", flaky),
			patch.object(frappe, "log_error") as log_error,
		):
			stats = reminders.run(at(0, 10, 30))

		self.assertEqual((stats["notified"], stats["errors"]), (1, 1))
		log_error.assert_called_once()
		self.assertIn(bad_chat, log_error.call_args.kwargs["title"])
		self.assertEqual(self.reminders(bad_chat), [])
		self.assertEqual(len(self.reminders(good_chat)), 1)
		self.assertFalse(frappe.db.exists("Notification Log", {"subject": "half done"}))

	def test_an_unexpected_failure_is_logged_and_raised_again(self):
		with (
			patch.object(reminders, "get_config", side_effect=RuntimeError("settings unreadable")),
			patch.object(frappe, "log_error") as log_error,
		):
			with self.assertRaises(RuntimeError):
				reminders.run(at(0, 10))
		log_error.assert_called_once()
		self.assertIn("reply reminder check failed", log_error.call_args.kwargs["title"])

	def test_disabled_does_nothing(self):
		self.config = cfg(enabled=False)
		self.add(at(0, 9))
		self.assertEqual(reminders.run(at(0, 15))["skipped"], "reminders are off")
		self.assertEqual(self.reminders(), [])
