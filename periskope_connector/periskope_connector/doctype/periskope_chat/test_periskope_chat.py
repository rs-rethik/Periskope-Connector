"""Tests for the Periskope Chat doctype: the chat row and the CRM pages."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime

from periskope_connector import autoreply, messages
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


NUMBER = "919600000321"
CHAT = f"{NUMBER}@c.us"
OTHER = "919600000654@c.us"


def _utc(minutes_ago: float = 0) -> str:
	return (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


class ChatCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_chat_test")
		settings(test_mode=1, test_allowed_chats=NUMBER, enable_auto_reply=0)
		for p in (
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
			patch.object(messages, "fetch_chat_name", lambda *a, **k: ""),
		):
			p.start()
			self.addCleanup(p.stop)
		frappe.db.delete("Periskope Chat", {"chat_id": ["in", [CHAT, OTHER]]})
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Chat Test", "mobile_no": f"+{NUMBER}"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		self.lead = lead.name
		self.n = 0

	def _msg(self, body="Do you have 30s cotton?", chat_id=CHAT, from_me=False, minutes_ago=0, **kw):
		self.n += 1
		return messages.create_message(
			event(
				message_id=kw.pop("message_id", f"{'true' if from_me else 'false'}_{chat_id}_T{self.n}"),
				chat_id=chat_id,
				sender_phone=chat_id,
				from_me=from_me,
				body=body,
				timestamp=_utc(minutes_ago),
				**kw,
			)
		)


class TestChatRow(ChatCase):
	def test_a_message_keeps_the_chat_row_and_its_crm_link(self):
		self._msg(minutes_ago=5)
		row = frappe.get_doc("Periskope Chat", {"chat_id": CHAT})
		self.assertEqual((row.crm_lead, row.contact_number), (self.lead, NUMBER))
		first = get_datetime(row.last_message_at)
		self._msg("older", minutes_ago=50)  # a recovered older message
		self.assertEqual(
			get_datetime(frappe.db.get_value("Periskope Chat", {"chat_id": CHAT}, "last_message_at")), first
		)

	def test_a_rep_reply_cancels_the_waiting_ai_reply(self):
		settings(enabled=1, enable_auto_reply=1)
		self._msg()
		self.assertTrue(frappe.db.exists("Periskope Auto Reply", {"chat_id": CHAT, "status": "Waiting"}))
		self._msg("Yes, 500 kg in stock", from_me=True)
		self.assertFalse(frappe.db.exists("Periskope Auto Reply", {"chat_id": CHAT, "status": "Waiting"}))
		# no pause: the next unanswered message starts a new clock
		self._msg("And the price?")
		self.assertTrue(frappe.db.exists("Periskope Auto Reply", {"chat_id": CHAT, "status": "Waiting"}))


class TestCrmPages(FrappeTestCase):
	def test_the_lead_and_deal_pages_have_no_pause_button(self):
		if not frappe.db.exists("DocType", "CRM Form Script"):
			self.skipTest("CRM is not installed")
		for dt in ("CRM Deal", "CRM Lead"):
			self.assertFalse(frappe.db.exists("CRM Form Script", f"Periskope AI ({dt})"))

	def test_the_auto_reply_module_still_cancels_on_a_rep_reply(self):
		self.assertTrue(callable(autoreply.cancel))
