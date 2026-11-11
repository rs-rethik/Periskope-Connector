"""The daily brief on Today (api/brief.py): built from the facts the person may see, worded by the AI
with the app's own links, a plain brief without the AI, written once a day and refreshed at most every
few minutes."""

from unittest.mock import patch

import frappe

from periskope_connector.ai import assistant
from periskope_connector.api import brief
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.test_crm_panel import PanelCase

UPSET = "919600044441"


class BriefCase(PanelCase):
	def setUp(self):
		super().setUp()
		self.chat = self._msg(contact=UPSET, body="Order is 10 days late, very disappointed")
		frappe.db.sql(
			"""update `tabPeriskope Message` set severity = 'High', sentiment = 'Negative',
			ai_category = 'complaint', ai_summary = 'Order 10 days late', message_timestamp = now()
			where periskope_chat = %s""",
			self.chat,
		)
		frappe.cache().delete_value(brief._key("Administrator"))
		self.addCleanup(frappe.cache().delete_value, brief._key("Administrator"))


class TestFacts(BriefCase):
	def test_an_unhappy_customer_is_a_fact_with_its_chat(self):
		facts = brief.gather("Administrator")
		urgent = [i for i in facts["items"] if i["id"].startswith("u")]
		self.assertIn(self.chat, [i["chat"] for i in urgent])
		self.assertEqual(next(i for i in urgent if i["chat"] == self.chat)["tag"], "complaint")

	def test_a_rep_sees_only_the_chats_they_may_open(self):
		frappe.set_user(_user("pkt.brief.rep@example.com", "Sales User"))
		urgent = [i for i in brief.gather(frappe.session.user)["items"] if i["id"].startswith("u")]
		self.assertNotIn(self.chat, [i.get("chat") for i in urgent])


class TestWriting(BriefCase):
	def test_the_ai_words_it_and_the_links_are_the_apps(self):
		def fake(system, user, schema, max_tokens=0):
			self.assertNotIn(self.chat, user)  # the AI never sees the chat names it could get wrong
			ref = next(i["id"] for i in frappe.parse_json(user)["items"] if i["id"].startswith("u"))
			return {
				"points": [{"text": "Call back the upset customer.", "ref": ref}, {"text": "x", "ref": "zz"}]
			}

		with (
			patch.object(assistant, "is_ready", return_value=True),
			patch.object(assistant, "complete_json", side_effect=fake),
		):
			out = brief.get_brief()
		self.assertTrue(out["by_ai"])
		self.assertEqual(out["points"][0]["text"], "Call back the upset customer.")
		self.assertTrue(out["points"][0]["chat"])
		self.assertNotIn("chat", out["points"][1])  # an id the AI made up gets no link

	def test_without_the_ai_a_plain_brief(self):
		with patch.object(assistant, "is_ready", return_value=False):
			out = brief.get_brief()
		self.assertFalse(out["by_ai"])
		self.assertTrue(any(p.get("chat") == self.chat for p in out["points"]))

	def test_an_ai_error_falls_back_to_the_plain_brief(self):
		with (
			patch.object(assistant, "is_ready", return_value=True),
			patch.object(assistant, "complete_json", side_effect=Exception("down")),
		):
			out = brief.get_brief()
		self.assertFalse(out["by_ai"])
		self.assertTrue(out["points"])

	def test_written_once_a_day_and_refreshed_at_most_every_few_minutes(self):
		with (
			patch.object(assistant, "is_ready", return_value=False),
			patch.object(brief, "write", wraps=brief.write) as spy,
		):
			first = brief.get_brief()
			brief.get_brief()
			brief.get_brief(refresh=1)  # too soon: the kept one
			self.assertEqual(spy.call_count, 1)
			kept = {**first, "generated_at": "2000-01-01 00:00:00"}
			frappe.cache().set_value(brief._key("Administrator"), kept)
			brief.get_brief(refresh=1)
			self.assertEqual(spy.call_count, 2)
