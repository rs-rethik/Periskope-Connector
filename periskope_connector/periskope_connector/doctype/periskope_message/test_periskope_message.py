from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import messages

# The CRM link fields would otherwise make the runner build test leads and deals.
test_ignore = ["CRM Lead", "CRM Deal"]


def _event(**data):
	base = {
		"message_id": "false_919786387999@c.us_ABC1",
		"chat_id": "919600000001@c.us",
		"from_me": False,
		"org_phone": "919786387999@c.us",
		"sender_phone": "919600000001@c.us",
		"message_type": "chat",
		"body": "Hi, what is the price?",
		"timestamp": "2026-09-29T06:30:00Z",
		"ack": "3",
	}
	base.update(data)
	return {"event_type": "message.created", "data": base, "org_id": "org-1"}


class TestPeriskopeMessage(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		names = patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Test Group")
		names.start()
		self.addCleanup(names.stop)
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_msg_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_msg_test")
		rep = patch.object(
			messages, "_rep_for_phone", lambda n: "Administrator" if n == "919786387999" else None
		)
		rep.start()
		self.addCleanup(rep.stop)

	def _indexes(self):
		"""{index name: [columns in order]} for the message table."""
		result = {}
		for row in frappe.db.sql("show index from `tabPeriskope Message`", as_dict=True):
			result.setdefault(row.Key_name, []).append((row.Seq_in_index, row.Column_name))
		return {name: [c for _, c in sorted(cols)] for name, cols in result.items()}

	def test_timestamp_and_chat_indexes_exist(self):
		indexes = list(self._indexes().values())
		self.assertIn(["message_timestamp"], indexes)  # reminders, list view, workspace card/chart
		self.assertIn(["chat_id", "message_timestamp"], indexes)  # the AI's chat-history lookup

	def test_index_hook_can_run_again_safely(self):
		from periskope_connector.periskope_connector.doctype.periskope_message.periskope_message import (
			on_doctype_update,
		)

		before = self._indexes()
		on_doctype_update()  # migrate runs this every time
		self.assertEqual(self._indexes(), before)

	def test_incoming_one_to_one(self):
		name = messages.create_message(_event())
		doc = frappe.get_doc("Periskope Message", name)
		self.assertEqual(doc.direction, "Incoming")
		self.assertEqual(doc.contact_number, "919600000001")
		self.assertEqual(doc.org_phone, "919786387999")
		self.assertEqual(doc.rep, "Administrator")
		self.assertEqual(doc.body, "Hi, what is the price?")
		self.assertFalse(doc.is_group)
		self.assertIsNotNone(doc.message_timestamp)

	def test_outgoing_keeps_customer_number(self):
		name = messages.create_message(_event(message_id="true_x_1", from_me=True))
		doc = frappe.get_doc("Periskope Message", name)
		self.assertEqual(doc.direction, "Outgoing")
		self.assertEqual(doc.contact_number, "919600000001")

	def test_group_has_no_customer_number(self):
		name = messages.create_message(_event(message_id="g_1", chat_id="1203630@g.us"))
		doc = frappe.get_doc("Periskope Message", name)
		self.assertTrue(doc.is_group)
		self.assertFalse(doc.contact_number)
		self.assertEqual(doc.sender_phone, "919600000001")

	def test_media_fields(self):
		media = {
			"filename": "quote.pdf",
			"mimetype": "application/pdf",
			"path": "org/x/quote.pdf",
			"size": 1234,
		}
		name = messages.create_message(_event(message_id="m_1", message_type="document", media=media))
		doc = frappe.get_doc("Periskope Message", name)
		self.assertEqual(
			(doc.media_filename, doc.media_mimetype, doc.media_size), ("quote.pdf", "application/pdf", 1234)
		)

	def test_duplicate_message_is_skipped(self):
		self.assertTrue(messages.create_message(_event()))
		self.assertIsNone(messages.create_message(_event()))
		self.assertEqual(
			frappe.db.count("Periskope Message", {"message_id": _event()["data"]["message_id"]}), 1
		)

	def test_payload_without_message_id_is_ignored(self):
		self.assertIsNone(messages.create_message({"event_type": "message.created", "data": {}}))

	def test_timestamp_converted_to_site_timezone(self):
		dt = messages._to_site_datetime("2026-09-29T06:30:00Z")
		self.assertIsNone(dt.tzinfo)
		self.assertIsNone(messages._to_site_datetime("not a date"))

	def test_message_type_is_one_of_five_and_the_raw_type_is_kept(self):
		for raw, expected in (
			("chat", "text"),
			("image", "image"),
			("document", "document"),
			("ptt", "audio"),
			("audio", "audio"),
			("video", "other"),
			("sticker", "other"),
		):
			doc = frappe.get_doc(
				"Periskope Message",
				messages.create_message(_event(message_id=f"type_{raw}", message_type=raw)),
			)
			self.assertEqual((doc.message_type, doc.periskope_type), (expected, raw), raw)

	def test_customer_phone_is_the_last_ten_digits_of_the_customer_or_the_group_sender(self):
		one = frappe.get_doc("Periskope Message", messages.create_message(_event(message_id="cp1")))
		group = frappe.get_doc(
			"Periskope Message",
			messages.create_message(
				_event(message_id="cp2", chat_id="1203630@g.us", sender_phone="919811122233@c.us")
			),
		)
		self.assertEqual(one.customer_phone, "9600000001")
		self.assertEqual(group.customer_phone, "9811122233")

	def test_sender_name_is_the_chat_name_of_a_customer_message_only(self):
		with patch.object(messages, "fetch_chat_name", lambda *a, **k: "Meena Iyer"):
			incoming = messages.create_message(_event(message_id="sn1"))
			outgoing = messages.create_message(_event(message_id="sn2", from_me=True))
			group = messages.create_message(_event(message_id="sn3", chat_id="1203631@g.us"))
		names = [
			frappe.db.get_value("Periskope Message", n, "sender_name") for n in (incoming, outgoing, group)
		]
		self.assertEqual(names, ["Meena Iyer", "", ""])

	def test_a_rep_reply_marks_the_earlier_customer_messages_as_replied(self):
		first = messages.create_message(_event(message_id="rp1", timestamp="2026-09-29T06:30:00Z"))
		other_chat = messages.create_message(
			_event(message_id="rp2", chat_id="919600000002@c.us", timestamp="2026-09-29T06:31:00Z")
		)
		later = messages.create_message(_event(message_id="rp3", timestamp="2026-09-29T06:50:00Z"))
		messages.create_message(_event(message_id="rp4", from_me=True, timestamp="2026-09-29T06:40:00Z"))
		replied = lambda n: frappe.db.get_value("Periskope Message", n, "replied")  # noqa: E731
		self.assertEqual((replied(first), replied(other_chat), replied(later)), (1, 0, 0))

	def test_the_sales_person_of_the_phone_is_saved_on_the_message(self):
		with patch.object(messages, "_sales_person_for_phone", lambda n: "Sales Team"):
			name = messages.create_message(_event(message_id="sp1"))
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "sales_person"), "Sales Team")

	def test_a_sales_manager_can_set_the_correct_category_but_only_a_real_one(self):
		from periskope_connector import tags
		from periskope_connector.api import settings as settings_api
		from periskope_connector.patches.move_to_ai_categories import TAG_TO_CATEGORY

		name = messages.create_message(_event(message_id="cc1"))
		settings_api.set_correct_category(name, "delivery_delay")
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "correct_category"), "delivery_delay")
		with self.assertRaises(frappe.ValidationError):
			settings_api.set_correct_category(name, "not_a_category")
		self.assertTrue(set(TAG_TO_CATEGORY.values()) <= set(tags.names()))
