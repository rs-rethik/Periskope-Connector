"""Tests for the Assistant (api/help_bot.py): answers come from one AI call, and a report download is
only ever one the user may open, made by the server with checked dates."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, today

from periskope_connector.ai import assistant
from periskope_connector.api import help_bot
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


class HelpBotCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_help_bot_test")
		self.addCleanup(frappe.set_user, "Administrator")
		self.rep = "helpbot.rep@example.com"
		if not frappe.db.exists("User", self.rep):
			frappe.get_doc(
				{"doctype": "User", "email": self.rep, "first_name": "Rep", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", self.rep).add_roles("Sales User")

	def ask(self, answer, question="hi", history=None):
		with (
			patch.object(assistant, "readiness", return_value=(True, "")),
			patch.object(assistant, "complete_json", return_value=answer) as ai,
		):
			return help_bot.ask(question, history), ai


class TestAsk(HelpBotCase):
	def test_answer_without_a_download(self):
		out, ai = self.ask({"reply": "Open the chat and click Assign.", "download": None})
		self.assertEqual(out, {"reply": "Open the chat and click Assign.", "download": None})
		system, user = ai.call_args[0][:2]
		self.assertIn("## User Manual", system)
		self.assertIn("Periskope Response Time", system)
		self.assertIn("Open chats waiting for a reply", system)
		self.assertIn("company WhatsApp numbers this user can see", system)
		self.assertIn("Test mode", system)
		self.assertEqual(user, "hi")

	def test_history_goes_with_the_question(self):
		_out, ai = self.ask(
			{"reply": "ok", "download": None},
			"and as CSV?",
			[{"role": "user", "text": "response time report"}, {"role": "assistant", "text": "Here it is."}],
		)
		user = ai.call_args[0][1]
		self.assertIn("User: response time report", user)
		self.assertIn("Assistant: Here it is.", user)
		self.assertTrue(user.endswith("and as CSV?"))

	def test_download_link_for_a_report(self):
		out, _ai = self.ask(
			{
				"reply": "Here is the Response Time report.",
				"download": {
					"report": "Periskope Response Time",
					"from_date": "2026-10-01",
					"to_date": "2026-10-31",
					"format": "csv",
				},
			}
		)
		d = out["download"]
		self.assertEqual((d["from_date"], d["to_date"], d["format"]), ("2026-10-01", "2026-10-31", "csv"))
		self.assertEqual(d["label"], "Response Time")
		self.assertIn("help_bot.download_report?report=Periskope+Response+Time", d["url"])

	def test_unknown_report_or_bad_dates_are_fixed_or_dropped(self):
		out, _ai = self.ask(
			{
				"reply": "x",
				"download": {"report": "Sales Register", "from_date": "", "to_date": "", "format": "xlsx"},
			}
		)
		self.assertIsNone(out["download"])
		out, _ai = self.ask(
			{
				"reply": "x",
				"download": {
					"report": "Periskope Lead Source",
					"from_date": "not a date",
					"to_date": "",
					"format": "pdf",
				},
			}
		)
		d = out["download"]
		self.assertEqual(d["format"], "xlsx")
		self.assertEqual((d["from_date"], d["to_date"]), (str(add_days(today(), -28)), today()))
		out, _ai = self.ask(
			{
				"reply": "x",
				"download": {
					"report": "Periskope Open Escalations",
					"from_date": "2026-01-01",
					"to_date": "2026-01-31",
					"format": "xlsx",
				},
			}
		)
		self.assertEqual((out["download"]["from_date"], out["download"]["to_date"]), ("", ""))

	def test_reps_get_answers_but_no_reports(self):
		frappe.set_user(self.rep)
		self.assertEqual(help_bot.allowed_reports(), [])
		out, ai = self.ask(
			{
				"reply": "x",
				"download": {
					"report": "Periskope Response Time",
					"from_date": "",
					"to_date": "",
					"format": "xlsx",
				},
			}
		)
		self.assertIsNone(out["download"])
		self.assertIn("none: this user may not download any report", ai.call_args[0][0])
		self.assertRaises(frappe.PermissionError, help_bot.download_report, "Periskope Response Time")

	def test_no_ai_key_says_so_without_calling_the_ai(self):
		with (
			patch.object(assistant, "readiness", return_value=(False, "no_key")),
			patch.object(assistant, "complete_json") as ai,
		):
			out = help_bot.ask("hello")
		ai.assert_not_called()
		self.assertIn("AI key", out["reply"])
		self.assertRaises(frappe.ValidationError, help_bot.ask, "   ")


class TestNumbers(HelpBotCase):
	def test_numbers_by_service_and_state(self):
		rows = [
			frappe._dict(
				name="919000000001",
				number="919000000001",
				display_name="Sales",
				is_active=1,
				source="Meta",
				is_ready=1,
				disconnected_since=None,
				last_message_on=None,
				default_assignee=None,
				default_team=None,
			),
			frappe._dict(
				name="919000000002",
				number="919000000002",
				display_name="",
				is_active=1,
				source="Periskope",
				is_ready=0,
				disconnected_since="2026-11-06 10:00:00",
				last_message_on=None,
				default_assignee=None,
				default_team="North",
			),
			frappe._dict(
				name="919000000003",
				number="919000000003",
				display_name="",
				is_active=1,
				source="Periskope",
				is_ready=1,
				disconnected_since=None,
				last_message_on=None,
				default_assignee=None,
				default_team=None,
			),
		]
		with (
			patch.object(frappe, "get_all", return_value=rows),
			patch.object(help_bot.access, "allowed_numbers", return_value=["919000000001", "919000000002"]),
		):
			text = help_bot._numbers()
		self.assertIn(
			"Connected through Meta's WhatsApp API (1):\n  - +919000000001 (Sales), connected", text
		)
		self.assertIn("NOT connected since 2026-11-06 10:00:00, its team is North", text)
		self.assertNotIn("919000000003", text)  # not one of the user's numbers


class TestDownload(HelpBotCase):
	def test_excel_and_csv(self):
		help_bot.download_report("Periskope Response Time", "xlsx", "2026-10-01", "2026-10-31")
		self.assertEqual(frappe.response["type"], "binary")
		self.assertEqual(frappe.response["filename"], "Response_Time_2026-10-01_to_2026-10-31.xlsx")
		self.assertTrue(frappe.response["filecontent"].startswith(b"PK"))
		help_bot.download_report("Periskope Open Escalations", "csv")
		self.assertEqual(frappe.response["type"], "csv")
		self.assertTrue(frappe.response["result"].startswith('"Reminder"'))

	def test_only_the_assistants_reports(self):
		self.assertRaises(frappe.PermissionError, help_bot.download_report, "General Ledger")
