"""Keyword alerts (M4): word-boundary matching, per-rule recipients, dedupe window."""

from datetime import datetime, timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import keywords, messages
from periskope_connector.system import insert_as_system
from periskope_connector.tests.test_linking import _event


def _rule(name="Legal", words="legal, lawyer", recipients="boss@example.com"):
	return frappe._dict(
		name=f"settings-{name}",
		rule_name=name,
		pattern=keywords.pattern_for([w.strip() for w in words.split(",")]),
		recipients=[recipients] if recipients else [],
	)


class TestPattern(FrappeTestCase):
	def test_whole_words_only_case_insensitive(self):
		p = keywords.pattern_for(["po", "payment pending"])
		for hit in ("PO", "po.", "PO-123", "send po now", "Payment Pending!"):
			self.assertTrue(p.search(hit), hit)
		for miss in ("post", "potato", "important", "please pay the pending amount"):
			self.assertFalse(p.search(miss), miss)

	def test_tamil_words_match_over_tamil_text_boundaries(self):
		p = keywords.pattern_for(["தரம் இல்லை", "sari"])
		self.assertTrue(p.search("இந்த தரம் இல்லை முதலாளி"))
		self.assertTrue(p.search("Sari sir, we will pay tomorrow"))

	def test_punctuated_keywords_do_not_get_broken_boundaries(self):
		p = keywords.pattern_for(["p.o."])
		self.assertTrue(p.search("send the P.O. today"))
		self.assertFalse(p.search("p.o.box"))

	def test_phrases_win_over_their_parts(self):
		p = keywords.pattern_for(["quality", "quality problem"])
		self.assertTrue(p.search("a quality problem in the lot"))


class TestCheckMessage(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch.object(messages, "_rep_for_phone", lambda n: None),
			# create_message runs the keyword check itself; these tests call it explicitly, so
			# keep the ingest-time call quiet to avoid double-firing and dedupe suppression
			patch.object(messages, "check_message", lambda *a, **k: 0),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_keyword_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_keyword_test")

	def _message(self, message_id, body, **data):
		name = messages.create_message(_event(message_id=message_id, body=body, **data))
		return name

	def test_a_matching_message_alerts_the_rules_recipients_once(self):
		with (
			patch.object(keywords, "rules", lambda: [_rule()]),
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 6),
			patch.object(frappe, "sendmail") as sendmail,
		):
			name = self._message("kw1", "We will send a legal notice for this rejection")
			self.assertEqual(keywords.check_message(name), 1)
			kwargs = sendmail.call_args.kwargs
			self.assertEqual(kwargs["recipients"], ["boss@example.com"])
			self.assertIn("legal notice", kwargs["message"])

			# the alert row exists and dedupes a second run over the same message
			self.assertEqual(frappe.db.count("Periskope Keyword Alert", {"message": name}), 1)
			self.assertEqual(keywords.check_message(name), 0)
			self.assertEqual(sendmail.call_count, 1)

	def test_a_second_match_in_the_same_chat_inside_the_window_is_suppressed(self):
		with (
			patch.object(keywords, "rules", lambda: [_rule()]),
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 6),
			patch.object(frappe, "sendmail") as sendmail,
		):
			first = self._message("kw2", "legal action coming")
			second = self._message("kw3", "still legal action")
			self.assertEqual(keywords.check_message(first), 1)
			self.assertEqual(keywords.check_message(second), 0)  # same rule, same chat, inside 6 h
			self.assertEqual(sendmail.call_count, 1)

	def test_the_window_is_per_rule_and_per_chat(self):
		other = _rule(name="Quality", words="rejected", recipients="quality@example.com")
		with (
			patch.object(keywords, "rules", lambda: [_rule(), other]),
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 6),
			patch.object(frappe, "sendmail"),
		):
			name = self._message("kw4", "the lot is rejected, legal notice coming")
			self.assertEqual(keywords.check_message(name), 2)  # two different rules
			other_chat = self._message("kw5", "rejected again", chat_id="919600000002@c.us")
			self.assertEqual(keywords.check_message(other_chat), 1)  # other chat is not deduped

	def test_a_rule_without_recipients_never_sends(self):
		before = frappe.db.count("Periskope Keyword Alert")  # the site may already have real alerts
		with patch.object(keywords, "rules", lambda: [_rule(recipients="")]):
			name = self._message("kw6", "legal legal legal")
			self.assertEqual(keywords.check_message(name), 0)
			self.assertEqual(frappe.db.count("Periskope Keyword Alert"), before)

	def test_outgoing_and_empty_messages_are_not_checked(self):
		with patch.object(keywords, "rules") as rules:
			outgoing = self._message("kw7", "legal", from_me=True)
			self.assertEqual(keywords.check_message(outgoing), 0)
			rules.assert_not_called()

	def test_an_old_alert_does_not_suppress_a_new_one(self):
		insert_as_system(
			{
				"doctype": "Periskope Keyword Alert",
				"rule": "settings-Legal",
				"rule_name": "Legal",
				"chat_id": "919600000001@c.us",
				"sent_at": now_datetime() - timedelta(hours=7),  # outside the 6 h window
			}
		)
		with (
			patch.object(keywords, "rules", lambda: [_rule()]),
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 6),
			patch.object(frappe, "sendmail"),
		):
			name = self._message("kw8", "legal notice")
			self.assertEqual(keywords.check_message(name), 1)
