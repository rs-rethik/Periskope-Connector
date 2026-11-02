"""Fixes from the code review: numbers of other countries never meet a local lead by their last
digits, a Jarvis call counts in the token budget, and the chat analysis survives a failed or odd AI
reply."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import health, linking
from periskope_connector.ai import analyse, classifier
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


def _lead(mobile: str) -> str:
	doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Review Test", "mobile_no": mobile})
	doc.flags.ignore_mandatory = True
	doc.insert(ignore_permissions=True)
	return doc.name


class TestNumbers(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_review_numbers")
		frappe.local.cache.pop("periskope_home_code", None)

	def _found(self, number: str) -> set:
		return {lead for lead, _deal in linking.by_mobile(number)}

	def test_a_local_number_saved_any_way_is_found(self):
		bare = _lead("9876511111")
		coded = _lead("+91 98765 22222")
		self.assertIn(bare, self._found("919876511111"))
		self.assertIn(coded, self._found("919876522222"))
		self.assertIn(coded, self._found("9876522222"))  # typed without the code

	def test_a_foreign_number_never_meets_a_local_lead_by_its_last_digits(self):
		local = _lead("+91 98765 33333")
		self.assertNotIn(local, self._found("19876533333"))  # +1, same last 10 digits
		foreign = _lead("+1 987 653 3333")
		self.assertEqual(self._found("19876533333"), {foreign})

	def test_a_local_number_gets_the_companys_country_code(self):
		self.assertEqual(linking.with_country_code("98765 44444"), "919876544444")
		self.assertEqual(linking.with_country_code("098765 44444"), "919876544444")
		self.assertEqual(linking.with_country_code("+44 7700 900123"), "447700900123")


class TestJarvisBudget(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_review_jarvis")

	def test_a_jarvis_call_counts_in_the_monthly_tokens(self):
		before = health.month_tokens()
		fake = (lambda prompt, user, timeout=60: "x" * 400, RuntimeError)
		with patch.object(classifier, "_jarvis_llm_task", return_value=fake):
			classifier.jarvis_complete("p" * 4000, "u" * 4000)
		self.assertEqual(health.month_tokens() - before, 2000 + 100)


class TestAnalyse(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_review_analyse")
		self.chat = "919876555555@c.us"
		frappe.cache().delete_value(f"periskope_analysed:{self.chat}")

	def test_a_failed_ai_call_is_logged_and_tried_again_on_the_next_message(self):
		with (
			patch.object(analyse.crm_context, "build", return_value=frappe._dict(doctype="CRM Lead")),
			patch.object(analyse.assistant, "is_ready", return_value=True),
			patch.object(analyse.assistant, "complete_json", side_effect=RuntimeError("down")),
			patch.object(analyse, "_statuses", return_value=[]),
			patch.object(analyse.crm_context, "internal_text", return_value=""),
			patch.object(analyse, "_transcript", return_value=""),
		):
			self.assertEqual(analyse.run(self.chat), {"skipped": "the AI call failed"})
		self.assertFalse(frappe.cache().get_value(f"periskope_analysed:{self.chat}"))

	def test_odd_hours_from_the_ai_do_not_crash(self):
		self.assertEqual(analyse._hours("6"), 6)
		self.assertEqual(analyse._hours(2.5), 2)
		self.assertEqual(analyse._hours("tomorrow"), 4)
		self.assertEqual(analyse._hours(None), 4)


class TestAiOff(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_review_ai_off")
		self.chat_id = "919876566666@c.us"
		frappe.db.delete("Periskope Chat", {"chat_id": self.chat_id})

	def test_a_chat_kept_away_from_the_ai_is_not_tagged_answered_or_analysed(self):
		from periskope_connector import autoreply, chat
		from periskope_connector.ai import analyse

		row = frappe.get_doc(
			{"doctype": "Periskope Chat", "chat_id": self.chat_id, "chat_key": self.chat_id, "ai_off": 1}
		).insert(ignore_permissions=True)
		self.assertTrue(chat.ai_off(self.chat_id))
		message = frappe._dict(direction="Incoming", chat_id=self.chat_id, message_type="text", body="Hi")
		self.assertEqual(classifier.skip_reason(message, frappe._dict()), "AI is off for this chat")
		with patch.object(autoreply, "schedule") as schedule:
			self.assertEqual(autoreply.on_message(message), "")
			schedule.assert_not_called()
		self.assertEqual(analyse.run(self.chat_id), {"skipped": "AI is off for this chat"})
		frappe.db.set_value("Periskope Chat", row.name, "ai_off", 0)
		self.assertFalse(chat.ai_off(self.chat_id))


class TestSharedFilesAndAiAddress(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_review_files")

	def test_public_copies_for_meta_are_removed_after_a_day(self):
		from frappe.utils import add_to_date, now_datetime

		from periskope_connector import meta

		old = frappe.get_doc(
			{"doctype": "File", "file_name": meta.SHARE_PREFIX + "old.txt", "is_private": 0, "content": "a"}
		).insert(ignore_permissions=True)
		new = frappe.get_doc(
			{"doctype": "File", "file_name": meta.SHARE_PREFIX + "new.txt", "is_private": 0, "content": "b"}
		).insert(ignore_permissions=True)
		self.addCleanup(new.delete_file_from_filesystem)  # the database rolls back, the disk does not
		frappe.db.set_value("File", old.name, "creation", add_to_date(now_datetime(), days=-2))
		meta.remove_shared_files()
		self.assertFalse(frappe.db.exists("File", old.name))
		self.assertTrue(frappe.db.exists("File", new.name))

	def test_the_ai_key_goes_only_to_https_and_known_or_trusted_hosts(self):
		check = classifier.ai_address_problem
		self.assertEqual(check("", False), "")
		self.assertEqual(check("https://api.z.ai/api/anthropic", False), "")
		self.assertTrue(check("http://api.z.ai/api/anthropic", True))  # never plain http
		self.assertTrue(check("https://api.anthropc.com", False))  # a typo is caught
		self.assertEqual(check("https://llm.example.com", True), "")  # trusted by the admin
