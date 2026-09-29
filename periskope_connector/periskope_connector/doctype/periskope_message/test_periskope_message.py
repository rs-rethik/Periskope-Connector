from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import messages


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
