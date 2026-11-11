"""Message actions (api/message_actions.py) and broadcasts (api/broadcast.py): react, edit, delete,
forward and pin through Periskope with test mode applied; an expired file link renewed by Get Message;
a broadcast sent only to test numbers, followed and stopped. Periskope is never called for real."""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from periskope_connector.api import broadcast, inbox
from periskope_connector.api import message_actions as actions
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.test_crm_panel import CUSTOMER, A, PanelCase
from periskope_connector.tests.utils import settings

STRANGER = "919600077771"


class ActionsCase(PanelCase):
	def setUp(self):
		super().setUp()
		settings(
			enabled=1, test_mode=1, test_allowed_chats=CUSTOMER, base_url="https://api.periskope.test/v1"
		)
		self.chat = self._msg()
		self.calls = []
		self.reply = {}

		def fake(method, path, phone=None, json=None, **kw):
			self.calls.append((method, path, phone, json))
			return self.reply

		for target in (actions, broadcast):
			p = patch.object(target, "request", side_effect=fake)
			p.start()
			self.addCleanup(p.stop)

	def _ours(self, chat=None, minutes_ago=2, sent_by="Administrator"):
		chat = chat or self.chat
		row = frappe.db.get_value("Periskope Chat", chat, ["chat_id", "whatsapp_number"], as_dict=True)
		doc = frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": f"true_{row.chat_id}_X{frappe.generate_hash(length=6)}",
				"direction": "Outgoing",
				"message_type": "text",
				"body": "Rate is 240/kg",
				"message_timestamp": add_to_date(now_datetime(), minutes=-minutes_ago),
				"chat_id": row.chat_id,
				"org_phone": row.whatsapp_number,
				"whatsapp_number": row.whatsapp_number,
				"periskope_chat": chat,
				"sent_by": sent_by,
			}
		).insert(ignore_permissions=True)
		return doc


class TestActions(ActionsCase):
	def test_react_and_take_it_back(self):
		m = self._ours()
		row = actions.react(m.name, "👍")
		self.assertEqual(self.calls[-1][1:], (f"message/{m.message_id}/react", A, {"reaction": "👍"}))
		self.assertEqual(row.reactions, {A: "👍"})
		self.assertEqual(actions.react(m.name, "").reactions, {})
		self.assertEqual(self.calls[-1][3], {"reaction": None})

	def test_edit_our_message_within_15_minutes(self):
		m = self._ours()
		row = actions.edit(m.name, "Rate is 235/kg")
		self.assertEqual(self.calls[-1][3], {"edited_body": "Rate is 235/kg"})
		self.assertEqual(
			(row.body, frappe.db.get_value("Periskope Message", m.name, "previous_body")),
			("Rate is 235/kg", "Rate is 240/kg"),
		)
		late = self._ours(minutes_ago=20)
		with self.assertRaises(frappe.ValidationError):
			actions.edit(late.name, "too late")

	def test_delete_for_everyone_only_our_own_and_only_by_its_sender_or_a_manager(self):
		m = self._ours(sent_by="Administrator")
		rep = _user("pkt.actions.rep@example.com", "Sales User")
		frappe.db.set_value("Periskope Chat", self.chat, "_assign", frappe.as_json([rep]))
		frappe.set_user(rep)
		with self.assertRaises(frappe.PermissionError):
			actions.delete(m.name)
		frappe.set_user("Administrator")
		self.assertTrue(actions.delete(m.name).is_deleted)
		self.assertEqual(self.calls[-1][:2], ("POST", f"message/{m.message_id}/delete"))

	def test_test_mode_keeps_other_customers_out(self):
		stranger = self._msg(contact=STRANGER)
		m = self._ours(chat=stranger)
		for call in (lambda: actions.react(m.name, "👍"), lambda: actions.pin(m.name, 1, 7)):
			with self.assertRaises(frappe.ValidationError):
				call()
		with self.assertRaises(frappe.ValidationError):
			actions.forward(self._ours().name, frappe.as_json([stranger]))
		self.assertEqual(self.calls, [])

	def test_forward_to_chats_of_the_same_number_and_pin(self):
		other = self._msg(contact=CUSTOMER)  # the same customer's chat (allowed)
		m = self._ours()
		self.reply = {f"{CUSTOMER}@c.us": True}
		out = actions.forward(m.name, frappe.as_json([other]))
		self.assertTrue(out["results"][0]["ok"])
		self.assertEqual(self.calls[-1][3], {"forward_chat_ids": [f"{CUSTOMER}@c.us"]})
		actions.pin(m.name, 1, 30)
		self.assertEqual(self.calls[-1][3], {"pin": True, "time": 30})
		with self.assertRaises(frappe.ValidationError):
			actions.pin(m.name, 1, 3)

	def test_an_expired_file_link_is_renewed(self):
		m = self._ours()
		frappe.db.set_value(
			"Periskope Message", m.name, {"message_type": "image", "media_path": "https://old/x.jpg"}
		)
		doc = frappe._dict(
			name=m.name, media_path="https://old/x.jpg", message_id=m.message_id, whatsapp_number=A
		)
		self.reply = {"media": {"path": "https://new/x.jpg"}}

		def download(url, limit=0):
			if "old" in url:
				raise Exception("expired")
			return b"jpg"

		with patch.object(inbox.attachments, "download", side_effect=download):
			self.assertEqual(inbox._download_media(doc), b"jpg")
		self.assertEqual(frappe.db.get_value("Periskope Message", m.name, "media_path"), "https://new/x.jpg")


class TestBroadcast(ActionsCase):
	def test_a_broadcast_to_test_numbers_followed_and_stopped(self):
		self.reply = {"broadcast_id": "B1"}
		b = broadcast.send(A, frappe.as_json([self.chat]), message="Hi {{name}}, new rates attached")
		body = self.calls[-1][3]
		self.assertEqual(
			(body["chat_ids"], body["org_phones"], body["delay"]), ([f"{CUSTOMER}@c.us"], [A], 3)
		)
		self.assertEqual(body["variables"][0]["chat_id"], f"{CUSTOMER}@c.us")
		self.assertEqual(b["status"], "Sending")
		self.reply = [
			{"chat_id": f"{CUSTOMER}@c.us", "status": "delivered", "message_id": "none"},
		]
		out = broadcast.progress("B1")
		self.assertEqual((out["broadcast"]["delivered"], out["broadcast"]["status"]), (1, "Done"))
		frappe.db.set_value("Periskope Broadcast", "B1", "status", "Sending")
		self.reply = {"status": "stopped"}
		self.assertEqual(broadcast.stop("B1")["status"], "Stopped")

	def test_test_mode_and_placeholders_and_managers(self):
		stranger = self._msg(contact=STRANGER)
		with self.assertRaises(frappe.ValidationError):
			broadcast.send(A, frappe.as_json([stranger]), message="Hi")
		with self.assertRaises(frappe.ValidationError):
			broadcast.send(A, frappe.as_json([self.chat]), message="Hi {{company}}")
		listed = broadcast.recipients(A, frappe.as_json({}))
		allowed = {c.name: c.allowed for c in listed["chats"]}
		self.assertEqual((allowed[self.chat], allowed[stranger]), (True, False))
		frappe.set_user(_user("pkt.broadcast.rep@example.com", "Sales User"))
		with self.assertRaises(frappe.PermissionError):
			broadcast.send(A, frappe.as_json([self.chat]), message="Hi")
		self.assertEqual(self.calls, [])
