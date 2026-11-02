"""Meta WhatsApp in the WhatsApp app (meta.py): the connected account's messages are read from
frappe_whatsapp into chats, replies go out through frappe_whatsapp (free text only within 24 hours,
templates any time), and the history import runs nothing automatic."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from periskope_connector import meta, numbers
from periskope_connector.api import compose, inbox
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES
ACCOUNT = "_Test Meta Account"
OURS = "919000000071"
CUSTOMER = "919600071071"
CHAT = f"{CUSTOMER}@c.us"
SEND = "frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message.make_post_request"


class MetaCase(FrappeTestCase):
	def setUp(self):
		if not meta.installed():
			self.skipTest("frappe_whatsapp is not installed")
		isolate(self, "periskope_meta_test")
		if not frappe.db.exists("WhatsApp Account", ACCOUNT):
			frappe.get_doc(
				{
					"doctype": "WhatsApp Account",
					"account_name": ACCOUNT,
					"status": "Active",
					"url": "https://graph.example.invalid",
					"version": "v0",
					"phone_id": "1",
					"token": "x",
				}
			).insert(ignore_permissions=True)
		settings(
			meta_enabled=1,
			meta_account=ACCOUNT,
			meta_number=OURS,
			test_mode=1,
			test_allowed_chats=CUSTOMER,
			enable_auto_reply=0,
			welcome_new_numbers=0,
		)
		frappe.clear_document_cache("Periskope Settings", "Periskope Settings")
		numbers.ensure(OURS)
		frappe.db.set_value(
			"WhatsApp Number", OURS, {"source": meta.SOURCE, "whatsapp_account": ACCOUNT, "is_ready": 1}
		)
		self.n = 0

	def _wm(self, incoming=True, text="Do you have 30s cotton?", minutes_ago=1, **values) -> str:
		"""A WhatsApp Message as frappe_whatsapp saves it (written directly: no send to Meta)."""
		self.n += 1
		doc = frappe.new_doc("WhatsApp Message")
		doc.update(
			{
				"type": "Incoming" if incoming else "Outgoing",
				"from": "+91-96000 71071" if incoming else "",
				"to": "" if incoming else "9600071071",
				"profile_name": "Kumar Knits" if incoming else "",
				"message": text,
				"message_id": f"wamid.T{self.n}",
				"content_type": "text",
				"whatsapp_account": ACCOUNT,
				"status": "" if incoming else "delivered",
				**values,
			}
		)
		doc.set_new_name()
		stamp = add_to_date(now_datetime(), minutes=-minutes_ago)
		doc.creation = doc.modified = stamp
		doc.db_insert()
		return doc.name

	def _chat(self) -> str:
		return frappe.db.get_value("Periskope Chat", {"chat_id": CHAT, "whatsapp_number": OURS})


class TestReading(MetaCase):
	def test_a_customer_message_becomes_a_chat_on_the_meta_number(self):
		name = meta.ingest(self._wm())
		m = frappe.db.get_value(
			"Periskope Message",
			name,
			["chat_id", "direction", "sender_name", "whatsapp_number", "message_id", "meta_message"],
			as_dict=True,
		)
		self.assertEqual((m.chat_id, m.direction, m.sender_name), (CHAT, "Incoming", "Kumar Knits"))
		self.assertEqual(m.whatsapp_number, OURS)
		self.assertTrue(m.message_id.startswith(meta.PREFIX) and m.meta_message)
		self.assertEqual(meta.ingest(m.meta_message), name)  # copied once
		row = inbox._decorate(frappe.get_all("Periskope Chat", filters={"name": self._chat()}, fields=["*"]))[
			0
		]
		self.assertEqual(row.source, meta.SOURCE)
		self.assertTrue(row.window["open"])

	def test_only_the_connected_account_is_read_and_reactions_are_left_out(self):
		with patch.object(frappe, "enqueue") as enqueue:
			doc = frappe.get_doc("WhatsApp Message", self._wm())
			meta.on_insert(doc)
			enqueue.assert_called_once()
			doc.whatsapp_account = "Another Account"
			meta.on_insert(doc)
			doc.whatsapp_account, doc.content_type = ACCOUNT, "reaction"
			meta.on_insert(doc)
			self.assertEqual(enqueue.call_count, 1)
		settings(meta_enabled=0)
		frappe.clear_document_cache("Periskope Settings", "Periskope Settings")
		self.assertIsNone(meta.ingest(self._wm()))

	def test_delivery_ticks_follow_frappe_whatsapp(self):
		wm = self._wm(incoming=False, status="sent")
		name = meta.ingest(wm)
		doc = frappe.get_doc("WhatsApp Message", wm)
		doc.status = "read"
		meta.on_update(doc)
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "ack"), "4")

	def test_a_live_customer_message_goes_to_the_ai_and_the_auto_reply(self):
		with (
			patch("periskope_connector.messages.agent_on", return_value=True),
			patch("periskope_connector.ai.classifier.queue") as tag,
			patch("periskope_connector.messages.autoreply_on_message") as auto,
			patch("periskope_connector.messages.welcome_on_message") as welcome,
		):
			name = meta.ingest(self._wm())
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "classification_status"), "Pending")
		tag.assert_called_once_with(name)
		auto.assert_called_once()
		welcome.assert_called_once()

	def test_a_chat_our_own_send_starts_waits_on_the_customer(self):
		meta.ingest(self._wm(incoming=False, text="Your invoice is attached", minutes_ago=10))
		self.assertEqual(frappe.db.get_value("Periskope Chat", self._chat(), "status"), "Pending")
		meta.ingest(self._wm(text="Received, thanks. Need 50 more", minutes_ago=1))
		self.assertEqual(frappe.db.get_value("Periskope Chat", self._chat(), "status"), "Open")

	def test_the_account_can_be_picked_in_settings(self):
		from periskope_connector.api import admin

		field = next(f for f in admin._field_meta("Periskope Settings", ["meta_account"]))
		self.assertEqual((field["fieldtype"], field["options"]), ("Link", "WhatsApp Account"))
		self.assertIn(ACCOUNT, [r.value for r in admin.pick("WhatsApp Account", "Meta Account")])

	def test_numbers_typed_any_way_reach_one_chat(self):
		self.assertEqual(meta.phone_number("+91-96000 71071"), CUSTOMER)
		self.assertEqual(meta.phone_number("9600071071"), CUSTOMER)
		self.assertEqual(meta.phone_number("919600071071"), CUSTOMER)


class TestSending(MetaCase):
	def _reply(self, text="Yes, 30s is in stock"):
		with patch(SEND, return_value={"messages": [{"id": "wamid.SENT1"}]}) as post:
			saved = compose.send(self._chat(), text)
		return saved, post

	def test_a_reply_in_the_window_goes_through_frappe_whatsapp_once(self):
		meta.ingest(self._wm())
		saved, post = self._reply()
		self.assertEqual(saved.send_status, "Queued")
		body = frappe.parse_json(post.call_args.kwargs["data"])
		self.assertEqual((body["to"], body["text"]["body"]), (CUSTOMER, "Yes, 30s is in stock"))
		sent = frappe.db.get_value("WhatsApp Message", {"message_id": "wamid.SENT1"})
		self.assertEqual(frappe.db.get_value("WhatsApp Message", sent, "whatsapp_account"), ACCOUNT)
		meta.ingest(sent)  # the after_insert job: fills the inbox's row instead of adding one
		self.assertEqual(frappe.db.count("Periskope Message", {"periskope_chat": self._chat()}), 2)
		row = frappe.db.get_value(
			"Periskope Message", saved.name, ["message_id", "meta_message"], as_dict=True
		)
		self.assertEqual((row.message_id, row.meta_message), ("meta_wamid.SENT1", sent))

	def test_after_24_hours_only_a_template_goes(self):
		meta.ingest(self._wm(minutes_ago=26 * 60))
		saved, post = self._reply()
		post.assert_not_called()
		self.assertEqual(saved.send_status, "Failed")
		self.assertIn("24 hours", saved.send_error)
		template = frappe.new_doc("WhatsApp Templates")
		template.update(
			{
				"template_name": "_test_follow_up",
				"template": "Hi {{1}}, any update on {{2}}?",
				"sample_values": "Ravi,the order",  # what frappe_whatsapp needs to send the values
				"language_code": "en",
				"category": "UTILITY",
				"status": "APPROVED",
				"whatsapp_account": ACCOUNT,
			}
		)
		template.name = "_test_follow_up-en"
		template.db_insert()
		self.assertIn("_test_follow_up-en", [t["name"] for t in compose.meta_templates(self._chat())])
		self.assertRaises(
			frappe.ValidationError, compose.send_template, self._chat(), template.name, ["Kumar"]
		)
		with patch(SEND, return_value={"messages": [{"id": "wamid.TPL1"}]}) as post:
			row = compose.send_template(self._chat(), template.name, ["Kumar", "the sample"])
		self.assertEqual(frappe.parse_json(post.call_args.kwargs["data"])["type"], "template")
		self.assertEqual(row.body, "Hi Kumar, any update on the sample?")
		self.assertEqual((row.direction, row.sent_by), ("Outgoing", "Administrator"))

	def test_test_mode_stops_a_number_that_is_not_allowed(self):
		meta.ingest(self._wm())
		settings(test_allowed_chats="919999999999")
		saved, post = self._reply()
		post.assert_not_called()
		self.assertEqual(saved.send_status, "Not Sent")

	def test_periskope_never_syncs_the_meta_number_away(self):
		numbers.sync_from_periskope([])
		self.assertEqual(frappe.db.get_value("WhatsApp Number", OURS, "is_ready"), 1)
		self.assertNotIn(OURS, numbers.ready())


class TestHistory(MetaCase):
	def test_the_import_copies_once_runs_nothing_automatic_and_closes_old_chats(self):
		settings(enable_auto_reply=1, welcome_new_numbers=1)
		first = self._wm(minutes_ago=3 * 24 * 60)
		self._wm(incoming=False, text="Price list attached", minutes_ago=3 * 24 * 60 - 5)
		with (
			patch("periskope_connector.messages.autoreply_on_message") as auto,
			patch("periskope_connector.messages.welcome_on_message") as welcome,
			patch.object(meta, "account", return_value=ACCOUNT),
		):
			copied = meta.import_history(since=str(add_to_date(now_datetime(), days=-4)))
			auto.assert_not_called()
			welcome.assert_not_called()
		self.assertGreaterEqual(copied, 2)
		name = frappe.db.get_value("Periskope Message", {"meta_message": first})
		self.assertFalse(frappe.db.get_value("Periskope Message", name, "classification_status"))
		self.assertEqual(frappe.db.get_value("Periskope Chat", self._chat(), "status"), "Resolved")
		with patch.object(meta, "account", return_value=ACCOUNT):
			self.assertEqual(meta.import_history(since=str(add_to_date(now_datetime(), days=-4))), 0)
