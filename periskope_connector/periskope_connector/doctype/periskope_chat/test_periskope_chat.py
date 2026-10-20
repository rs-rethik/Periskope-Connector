"""Tests for the Periskope Chat doctype: the chat row, the CRM WhatsApp tab copy and a rep's reply
from that tab."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import get_datetime, now_datetime

from periskope_connector import autoreply, chat, messages, mirror, outbound
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
		row = frappe.get_doc("Periskope Chat", CHAT)
		self.assertEqual((row.crm_lead, row.contact_number), (self.lead, NUMBER))
		first = get_datetime(row.last_message_at)
		self._msg("older", minutes_ago=50)  # a recovered older message
		self.assertEqual(get_datetime(frappe.db.get_value("Periskope Chat", CHAT, "last_message_at")), first)

	def test_a_rep_reply_cancels_the_waiting_ai_reply(self):
		settings(enabled=1, enable_auto_reply=1)
		self._msg()
		self.assertTrue(frappe.db.exists("Periskope Auto Reply", {"chat_id": CHAT, "status": "Waiting"}))
		self._msg("Yes, 500 kg in stock", from_me=True)
		self.assertFalse(frappe.db.exists("Periskope Auto Reply", {"chat_id": CHAT, "status": "Waiting"}))
		# no pause: the next unanswered message starts a new clock
		self._msg("And the price?")
		self.assertTrue(frappe.db.exists("Periskope Auto Reply", {"chat_id": CHAT, "status": "Waiting"}))


class TestWhatsAppTab(ChatCase):
	def setUp(self):
		super().setUp()
		mirror.ensure_account()
		if not mirror.available():
			self.skipTest("frappe_whatsapp is not installed")

	def _copy(self, message):
		name = frappe.db.get_value("Periskope Message", message, "whatsapp_message")
		return frappe.get_doc("WhatsApp Message", name) if name else None

	def test_messages_of_an_allowed_chat_show_in_order_with_ai_replies_marked(self):
		first = self._msg("Price of 30s cotton?", minutes_ago=20)
		copy = self._copy(first)
		self.assertEqual(
			(copy.type, copy.reference_doctype, copy.reference_name), ("Incoming", "CRM Lead", self.lead)
		)
		self.assertEqual(copy.whatsapp_account, mirror.ACCOUNT)
		self.assertLess(get_datetime(copy.creation), now_datetime() - timedelta(minutes=15))
		reply = self._msg("It is 245 per kg.", from_me=True)
		frappe.db.set_value("Periskope Message", reply, "agent_reply", "Answer")
		mirror.upsert(reply)
		self.assertTrue(self._copy(reply).message.startswith(mirror.AI_PREFIX))
		self.assertEqual(frappe.db.count("WhatsApp Message", {"message_id": self._copy(reply).message_id}), 1)

	def test_other_chats_are_not_copied_in_test_mode(self):
		other = self._msg(chat_id=OTHER)
		self.assertIsNone(self._copy(other))

	def test_a_rep_reply_from_the_tab_goes_through_periskope(self):
		self._msg()
		with patch.object(outbound, "send", return_value={"unique_id": "3EBREP"}) as send:
			doc = frappe.get_doc(
				{
					"doctype": "WhatsApp Message",
					"type": "Outgoing",
					"to": f"+{NUMBER}",
					"message": "Sure, sending the price list now.",
					"content_type": "text",
					"reference_doctype": "CRM Lead",
					"reference_name": self.lead,
				}
			).insert(ignore_permissions=True)
		self.assertEqual(send.call_args.args[:2], (CHAT, "Sure, sending the price list now."))
		self.assertEqual(doc.message_id, f"true_{CHAT}_3EBREP")
		# Periskope's own copy of that message (the webhook) does not add a second row.
		echo = self._msg("Sure, sending the price list now.", from_me=True, message_id=doc.message_id)
		self.assertEqual(frappe.db.get_value("Periskope Message", echo, "whatsapp_message"), doc.name)

	def test_nothing_of_the_periskope_account_reaches_meta(self):
		from periskope_connector.overrides.whatsapp_message import PeriskopeWhatsAppMessage

		self.assertIs(frappe.get_doc({"doctype": "WhatsApp Message"}).__class__, PeriskopeWhatsAppMessage)
		parent = PeriskopeWhatsAppMessage.__mro__[1]
		for method in ("before_insert", "send_outgoing", "send_template", "notify", "send_read_receipt"):
			self.assertTrue(hasattr(parent, method), f"frappe_whatsapp no longer has {method}")
		with patch("frappe.integrations.utils.make_post_request") as meta, patch("requests.post") as post:
			self._msg()
			with patch.object(outbound, "send", return_value={"unique_id": "X1"}):
				frappe.get_doc(
					{
						"doctype": "WhatsApp Message",
						"type": "Outgoing",
						"to": f"+{NUMBER}",
						"message": "Hello",
						"reference_doctype": "CRM Lead",
						"reference_name": self.lead,
					}
				).insert(ignore_permissions=True)
		meta.assert_not_called()
		post.assert_not_called()

	def test_a_reply_goes_through_periskope_even_when_a_meta_number_is_the_default(self):
		# On a site with a real Meta account as the default, the CRM's reply went to Meta, from a
		# number the customer never wrote to.
		frappe.db.set_value(
			"WhatsApp Account", mirror.ACCOUNT, {"is_default_outgoing": 0, "is_default_incoming": 0}
		)
		frappe.get_doc(
			{
				"doctype": "WhatsApp Account",
				"account_name": "Meta Test",
				"status": "Active",
				"url": "https://graph.facebook.invalid",
				"version": "v17.0",
				"phone_id": "meta-test",
				"token": "x",
				"is_default_outgoing": 1,
				"is_default_incoming": 1,
			}
		).insert(ignore_permissions=True)
		self._msg()
		with (
			patch("frappe.integrations.utils.make_post_request") as meta,
			patch.object(outbound, "send", return_value={"unique_id": "3EBMETA"}) as send,
		):
			doc = frappe.get_doc(
				{
					"doctype": "WhatsApp Message",
					"type": "Outgoing",
					"to": f"+{NUMBER}",
					"message": "Will check and let you know",
					"content_type": "text",
					"reference_doctype": "CRM Lead",
					"reference_name": self.lead,
				}
			).insert(ignore_permissions=True)
		self.assertEqual(doc.whatsapp_account, mirror.ACCOUNT)
		send.assert_called_once()
		meta.assert_not_called()

	def test_a_rep_cannot_send_to_a_chat_test_mode_does_not_allow(self):
		other_lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Other", "mobile_no": "+919600000654"}
		)
		other_lead.flags.ignore_mandatory = True
		other_lead.insert(ignore_permissions=True)
		self._msg(chat_id=OTHER)
		with patch("periskope_connector.client.requests.request") as http:
			with self.assertRaises(frappe.ValidationError):
				frappe.get_doc(
					{
						"doctype": "WhatsApp Message",
						"type": "Outgoing",
						"to": "+919600000654",
						"message": "Hi",
						"reference_doctype": "CRM Lead",
						"reference_name": other_lead.name,
					}
				).insert(ignore_permissions=True)
		http.assert_not_called()


class TestCrmPages(FrappeTestCase):
	def test_the_lead_and_deal_pages_have_no_pause_button(self):
		if not frappe.db.exists("DocType", "CRM Form Script"):
			self.skipTest("CRM is not installed")
		for dt in ("CRM Deal", "CRM Lead"):
			self.assertFalse(frappe.db.exists("CRM Form Script", f"Periskope AI ({dt})"))

	def test_the_auto_reply_module_still_cancels_on_a_rep_reply(self):
		self.assertTrue(callable(autoreply.cancel))
