"""The admins' knowledge base chat (kb_chat.py): tools, preview, confirm, documents, permissions."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import kb_chat
from periskope_connector.tests.utils import isolate


def _reply(text="", changes=None):
	return {"action": "reply", "tool": "", "args_json": "{}", "reply": text, "changes": changes or []}


def _tool(name, args="{}"):
	return {"action": "tool", "tool": name, "args_json": args, "reply": "", "changes": []}


class TestKnowledgeChat(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_kb_chat_test")
		ready = patch.object(kb_chat.assistant, "readiness", return_value=(True, ""))
		ready.start()
		self.addCleanup(ready.stop)
		self.entry = frappe.get_doc(
			{
				"doctype": "Periskope Knowledge Entry",
				"question": "What is the delivery time to Chennai?",
				"answer": "Delivery to Chennai takes 2 days.",
				"keywords": "delivery chennai",
				"status": "Approved",
				"source": "Manual",
			}
		).insert(ignore_permissions=True)

	def test_a_statement_becomes_a_preview_and_is_saved_only_on_confirm(self):
		change = {
			"operation": "Add",
			"entry": "",
			"question": "What is the delivery time to other states?",
			"answer": "Delivery to other states takes 5 days.",
			"keywords": "delivery states",
		}
		with patch.object(
			kb_chat.assistant, "complete_json", return_value=_reply("I prepared one entry.", [change])
		):
			out = kb_chat.chat("Other states take 5 days")
		self.assertEqual(len(out["preview"]), 1)
		self.assertFalse(frappe.db.exists("Periskope Knowledge Entry", {"question": change["question"]}))
		self.assertEqual(kb_chat.confirm(out["token"])["saved"], 1)
		self.assertEqual(
			frappe.db.get_value("Periskope Knowledge Entry", {"question": change["question"]}, "status"),
			"Approved",
		)
		with self.assertRaises(frappe.ValidationError):
			kb_chat.confirm(out["token"])  # a preview is used once

	def test_the_ai_looks_things_up_before_changing_them(self):
		steps = [
			_tool("search_knowledge", '{"query": "Chennai delivery"}'),
			_reply(
				"Updated.",
				[
					{
						"operation": "Modify",
						"entry": self.entry.name,
						"question": "",
						"answer": "Delivery to Chennai takes 3 days.",
						"keywords": "",
					}
				],
			),
		]
		seen = []

		def fake(system, user, schema, max_tokens=0):
			seen.append(user)
			return steps[len(seen) - 1]

		with patch.object(kb_chat.assistant, "complete_json", side_effect=fake):
			out = kb_chat.chat("Change the Chennai delivery time to 3 days")
		self.assertEqual(out["tools"], ["search_knowledge"])
		self.assertIn(self.entry.name, seen[1])  # the search result went back to the AI
		self.assertEqual(out["preview"][0]["before"]["answer"], "Delivery to Chennai takes 2 days.")
		kb_chat.confirm(out["token"])
		self.assertEqual(
			frappe.db.get_value("Periskope Knowledge Entry", self.entry.name, "answer"),
			"Delivery to Chennai takes 3 days.",
		)

	def test_unknown_entries_are_dropped_and_remove_retires(self):
		changes = [
			{"operation": "Modify", "entry": "no-such-entry", "question": "x", "answer": "y", "keywords": ""},
			{"operation": "Remove", "entry": self.entry.name, "question": "", "answer": "", "keywords": ""},
		]
		with patch.object(kb_chat.assistant, "complete_json", return_value=_reply("ok", changes)):
			out = kb_chat.chat("Remove the Chennai entry")
		self.assertEqual([c["operation"] for c in out["preview"]], ["Remove"])
		kb_chat.confirm(out["token"])
		self.assertEqual(
			frappe.db.get_value("Periskope Knowledge Entry", self.entry.name, "status"), "Retired"
		)

	def test_an_uploaded_price_list_is_read(self):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "prices.csv",
				"content": "Item,Price per kg\n30s cotton,245\n40s polyester,180\n",
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		self.assertIn("30s cotton,245", kb_chat.document_text(file.file_url))
		seen = []
		with patch.object(
			kb_chat.assistant,
			"complete_json",
			side_effect=lambda s, u, *a, **k: seen.append(u) or _reply("ok"),
		):
			kb_chat.chat("Add these prices", file_url=file.file_url)
		self.assertIn("UPLOADED DOCUMENT", seen[0])

	def test_gaps_are_the_questions_answered_with_the_holding_message(self):
		frappe.get_doc(
			{
				"doctype": "Periskope Auto Reply",
				"chat_id": "919600000111@c.us",
				"status": "Holding Sent",
				"customer_text": "Do you do custom dyeing for 2000 kg?",
				"confidence": 0,
			}
		).insert(ignore_permissions=True)
		self.assertIn(
			"Do you do custom dyeing for 2000 kg?", [g["question"] for g in kb_chat.knowledge_gaps()]
		)

	def test_no_ai_means_a_plain_message_not_a_call(self):
		with (
			patch.object(kb_chat.assistant, "readiness", return_value=(False, "budget")),
			patch.object(kb_chat.assistant, "complete_json") as call,
		):
			out = kb_chat.chat("hi")
		call.assert_not_called()
		self.assertIn("budget", out["reply"])

	def test_only_admins_can_use_it(self):
		original = frappe.session.user
		self.addCleanup(frappe.set_user, original)
		was_testing = frappe.flags.in_test
		frappe.flags.in_test = False
		self.addCleanup(setattr, frappe.flags, "in_test", was_testing)
		frappe.set_user("Guest")
		for call in (lambda: kb_chat.chat("hi"), lambda: kb_chat.confirm("x")):
			with self.assertRaises(frappe.PermissionError):
				call()
