"""Tests for the Periskope Knowledge Entry doctype and the knowledge base the agent builds."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import knowledge
from periskope_connector.ai import assistant
from periskope_connector.tests.utils import isolate


def _entry(question="What is the minimum order quantity?", answer="100 pieces.", status="Approved", **kw):
	return frappe.get_doc(
		{
			"doctype": "Periskope Knowledge Entry",
			"question": question,
			"answer": answer,
			"status": status,
			**kw,
		}
	).insert()


class TestKnowledge(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_knowledge_test")
		frappe.db.delete("Periskope Knowledge Entry")
		frappe.db.delete("Periskope Suggestion")

	def test_similar_question_adds_evidence_instead_of_a_new_entry(self):
		entry = _entry()
		out = knowledge.record(
			{
				"question": "minimum order quantity please?",
				"answer": "100 pieces.",
				"keywords": "moq",
				"confidence": 90,
			},
			[],
		)
		self.assertEqual(out, "seen")
		self.assertEqual(frappe.db.count("Periskope Knowledge Entry"), 1)
		self.assertEqual(frappe.db.get_value("Periskope Knowledge Entry", entry.name, "evidence_count"), 2)

	def test_a_new_question_is_used_at_once(self):
		out = knowledge.record(
			{
				"question": "Do you ship to Dubai?",
				"answer": "Yes, by air and sea.",
				"keywords": "",
				"confidence": 85,
			},
			["MSG-1"],
		)
		self.assertEqual(out, "added")
		entry = frappe.get_all("Periskope Knowledge Entry", fields=["status", "source"])[0]
		self.assertEqual((entry.status, entry.source), ("Approved", "AI Learned"))
		self.assertTrue(knowledge.find("do you ship to dubai"))
		suggestion = frappe.get_all("Periskope Suggestion", fields=["kind", "operation", "status"])[0]
		self.assertEqual(
			(suggestion.kind, suggestion.operation, suggestion.status), ("Knowledge Entry", "Add", "Applied")
		)

	def test_low_confidence_is_dropped(self):
		self.assertEqual(knowledge.record({"question": "Q one?", "answer": "A", "confidence": 30}, []), "")
		self.assertEqual(frappe.db.count("Periskope Knowledge Entry"), 0)

	def test_a_different_answer_to_an_approved_question_is_suggested_as_a_change(self):
		_entry()
		out = knowledge.record(
			{
				"question": "What is the minimum order quantity?",
				"answer": "Now we need 500 metres for dyed fabric.",
				"keywords": "",
				"confidence": 90,
			},
			[],
		)
		self.assertEqual(out, "changed")
		self.assertEqual(frappe.get_all("Periskope Suggestion", pluck="operation"), ["Modify"])

	def test_find_uses_approved_entries_only(self):
		_entry()
		_entry("Do you give samples?", "Yes, a swatch is free.", status="Suggested")
		self.assertEqual(
			[e.answer for e in knowledge.find("what is your minimum order quantity")], ["100 pieces."]
		)
		self.assertEqual(knowledge.find("do you give samples"), [])

	def test_mine_reads_the_ai_answer(self):
		reply = {
			"entries": [
				{
					"question": "Lead time for knitted fabric?",
					"answer": "10 days.",
					"keywords": "",
					"confidence": 88,
					"evidence_chat": 1,
				}
			]
		}
		with patch.object(assistant, "complete_json", return_value=reply):
			counts = knowledge.mine("### Conversation 1", {1: ["M1"]})
		self.assertEqual(counts["added"], 1)

	def test_old_unused_entries_are_flagged_for_retirement(self):
		old = _entry("Old question about lorry?", "Old answer")
		past = add_days(now_datetime(), -120)
		frappe.db.set_value("Periskope Knowledge Entry", old.name, {"creation": past, "last_seen": past})
		fresh = _entry("Fresh question about dye?", "Fresh answer", last_seen=now_datetime())
		self.assertEqual(knowledge.flag_stale(), 1)
		self.assertEqual(frappe.get_all("Periskope Suggestion", pluck="target"), [old.name])
		self.assertNotEqual(old.name, fresh.name)
