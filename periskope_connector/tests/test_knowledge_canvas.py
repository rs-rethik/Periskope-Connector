"""The Knowledge Base canvas (knowledge_canvas.py) and the knowledge tree the auto reply reads."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import answer, knowledge, knowledge_canvas, suggestions
from periskope_connector.tests.utils import isolate


def _node(node_type, question, parent=None, answer_text="", status="Approved"):
	return frappe.get_doc(
		{
			"doctype": "Periskope Knowledge Entry",
			"node_type": node_type,
			"question": question,
			"answer": answer_text,
			"parent_entry": parent,
			"status": status,
			"source": "Manual",
		}
	).insert(ignore_permissions=True)


class TestKnowledgeCanvas(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_kb_canvas_test")
		frappe.db.delete("Periskope Knowledge Entry")
		self.delivery = _node("Topic", "Delivery")
		self.chennai = _node("Condition", "Customer is in Chennai", self.delivery.name)
		self.other = _node("Condition", "Otherwise", self.delivery.name)
		self.two = _node("Answer", "Delivery time", self.chennai.name, "Delivery takes 2 days.")
		self.five = _node("Answer", "Delivery time", self.other.name, "Delivery takes 5 days.")

	# --- The tree -------------------------------------------------------------------------------

	def test_a_node_only_hangs_where_it_fits(self):
		with self.assertRaises(frappe.ValidationError):
			_node("Condition", "Customer is in Delhi")  # a condition needs a topic
		with self.assertRaises(frappe.ValidationError):
			_node("Answer", "Under an answer", self.two.name, "x")
		with self.assertRaises(frappe.ValidationError):
			_node("Answer", "No answer text")

	def test_a_loop_is_refused(self):
		inner = _node("Topic", "Express", self.delivery.name)
		self.delivery.parent_entry = inner.name
		with self.assertRaises(frappe.ValidationError):
			self.delivery.save(ignore_permissions=True)

	def test_a_match_brings_every_branch_with_when_it_applies(self):
		found = {e.name: e for e in knowledge.find("how many days for delivery to chennai")}
		self.assertEqual(set(found), {self.two.name, self.five.name})
		self.assertEqual(found[self.two.name].title, "Delivery > Delivery time")
		self.assertIn("Applies only when: Customer is in Chennai.", found[self.two.name].text)
		self.assertIn("none of these is true: Customer is in Chennai.", found[self.five.name].text)

	def test_one_word_of_a_topic_brings_its_answers(self):
		frappe.db.set_value("Periskope Knowledge Entry", self.delivery.name, "keywords", "shipping, courier")
		names = {e.name for e in knowledge.find("We are in Mumbai, how long is shipping?")}
		self.assertEqual(names, {self.two.name, self.five.name})

	def test_a_busy_topic_does_not_crowd_out_another(self):
		products = _node("Topic", "Products")
		frappe.db.set_value("Periskope Knowledge Entry", products.name, "keywords", "yarn, polyester")
		for count in range(12):
			_node("Answer", f"Price of polyester {count}s yarn", products.name, f"{count} per kg")
		pricing = _node("Topic", "Pricing")
		frappe.db.set_value("Periskope Knowledge Entry", pricing.name, "keywords", "discount")
		bulk = _node("Condition", "The order is 1000 kg or more", pricing.name)
		discount = _node("Answer", "Bulk discount", bulk.name, "3% off.")
		names = [e.name for e in knowledge.find("We want 1200 kg of 30s polyester yarn, any discount?")]
		self.assertIn(discount.name, names)
		self.assertNotIn(self.two.name, names)  # no delivery branches for a pricing question

	def test_a_named_topic_brings_its_branches_even_when_another_answer_ranks_first(self):
		charges = _node("Answer", "Delivery charges", self.delivery.name, "Free above 500 kg.")
		frappe.db.set_value("Periskope Knowledge Entry", self.delivery.name, "keywords", "reach")
		names = {e.name for e in knowledge.find("We are in Coimbatore, when will it reach us?")}
		self.assertTrue({charges.name, self.two.name, self.five.name} <= names)

	def test_a_short_question_matches_on_one_keyword(self):
		pay = _node(
			"Answer",
			"What payment methods do you accept?",
			None,
			"NEFT or RTGS only. Cheques are not accepted.",
		)
		frappe.db.set_value("Periskope Knowledge Entry", pay.name, "keywords", "payment, neft, rtgs, cheques")
		self.assertIn(pay.name, [e.name for e in knowledge.find("Can I pay by cheque?")])
		self.assertNotIn(pay.name, [e.name for e in knowledge.find("delivery to chennai")])
		frappe.db.set_value("Periskope Knowledge Entry", self.delivery.name, "keywords", "courier")
		for city in ("Madurai", "Salem", "Trichy", "Vellore"):
			cond = _node("Condition", f"Customer is in {city}", self.delivery.name)
			_node("Answer", f"Delivery time to {city}", cond.name, "3 days.")
		mixed = "Do you accept cheque? Also who pays the courier?"
		self.assertIn(pay.name, [e.name for e in knowledge.find(mixed)])  # not crowded out by Delivery

	def test_an_answer_under_an_unapproved_node_is_not_used(self):
		frappe.db.set_value("Periskope Knowledge Entry", self.chennai.name, "status", "Suggested")
		names = [e.name for e in knowledge.find("delivery to chennai")]
		self.assertNotIn(self.two.name, names)
		self.assertIn(self.five.name, names)

	def test_the_reply_sees_the_conditions(self):
		seen = []
		reply = {"can_answer": True, "answer": "2 days", "confidence": 90, "used_sources": []}
		with patch.object(
			answer.assistant, "complete_json", side_effect=lambda s, u, *a: seen.append((s, u)) or reply
		):
			answer.build("delivery to chennai?")
		system, data = seen[0]
		self.assertIn("Applies only when", system)
		self.assertIn("Applies only when: Customer is in Chennai. Delivery takes 2 days.", data)

	# --- What the agent learns goes into the tree ------------------------------------------------

	def _mine(self, entries):
		seen = []

		def fake(system, user, schema):
			seen.append(user)
			return {"entries": entries}

		with (
			patch.object(knowledge.assistant, "complete_json", side_effect=fake),
			patch.object(knowledge, "rep_said", return_value=True),  # tested on its own in the entry tests
		):
			counts = knowledge.mine("### Conversation 1\nCustomer: hi", {1: ["M1"]})
		return counts, seen[0]

	def _learned(self, question, **extra):
		return {
			"question": question,
			"answer": "x",
			"keywords": "",
			"confidence": 90,
			"evidence_chat": 1,
			"place": "",
			"new_topic": "",
			**extra,
		}

	def test_the_agent_sees_the_tree_and_places_an_answer_under_a_condition(self):
		item = self._learned(
			"Delivery time to Chennai for samples?",
			answer="Samples reach Chennai in 1 day.",
			place=self.chennai.name,
		)
		counts, prompt = self._mine([item])
		self.assertIn(f"[{self.chennai.name}] Condition: Delivery > Customer is in Chennai", prompt)
		self.assertEqual(counts["added"], 1)
		entry = frappe.get_doc("Periskope Knowledge Entry", {"question": item["question"]})
		self.assertEqual((entry.parent_entry, entry.status), (self.chennai.name, "Approved"))
		sug = frappe.get_doc("Periskope Suggestion", {"target": entry.name})
		self.assertIn("Goes under: Delivery > Customer is in Chennai", sug.reason)
		self.assertEqual(sug.status, "Applied")

	def test_a_new_topic_is_used_at_once_with_its_answers(self):
		self._mine(
			[
				self._learned("What are your payment terms?", answer="50% advance.", new_topic="Payment"),
				self._learned("Do you take cheques?", answer="NEFT or RTGS only.", new_topic="payment"),
			]
		)
		topic = frappe.get_doc("Periskope Knowledge Entry", {"node_type": "Topic", "question": "Payment"})
		self.assertEqual(topic.status, "Approved")
		answers = frappe.get_all("Periskope Knowledge Entry", {"parent_entry": topic.name}, pluck="name")
		self.assertEqual(len(answers), 2)  # one topic for both
		self.assertTrue(knowledge.find("what are the payment terms, cheques ok?"))

	def test_an_answer_the_ai_says_it_replaces_becomes_a_change(self):
		samples = _node("Topic", "Samples")
		old = _node(
			"Answer", "Do you send samples?", samples.name, "Up to 2 kg free; the customer pays the courier."
		)
		item = self._learned(
			"Are samples free?",
			answer="1 kg free and we pay the courier.",
			place=samples.name,
			replaces=old.name,
		)
		counts, prompt = self._mine([item])
		self.assertIn(f"[{old.name}] Answer under Samples: Do you send samples?", prompt)
		self.assertEqual(counts, {"added": 0, "seen": 0, "changed": 1})
		sug = frappe.get_doc("Periskope Suggestion", {"target": old.name, "operation": "Modify"})
		self.assertIn("1 kg", sug.payload)
		self.assertEqual(
			frappe.db.get_value("Periskope Knowledge Entry", old.name, "answer"),
			"1 kg free and we pay the courier.",
		)

	def test_an_answer_copied_from_the_tree_or_without_a_conversation_is_not_learned(self):
		copied = self._learned("How long to deliver in Chennai?", answer="Delivery takes 2 days.")
		unsourced = self._learned("Do you export?", answer="Yes.", evidence_chat=7)
		counts, _prompt = self._mine([copied, unsourced])
		self.assertEqual(counts, {"added": 0, "seen": 1, "changed": 0})
		self.assertFalse(frappe.db.exists("Periskope Knowledge Entry", {"question": "Do you export?"}))

	def test_a_new_case_becomes_a_new_condition_used_at_once(self):
		item = self._learned(
			"Delivery time to Bengaluru?",
			answer="4 working days.",
			place=self.delivery.name,
			new_condition="Customer is in Bengaluru",
		)
		self._mine([item])
		cond = frappe.get_doc(
			"Periskope Knowledge Entry", {"node_type": "Condition", "question": "Customer is in Bengaluru"}
		)
		self.assertEqual((cond.parent_entry, cond.status), (self.delivery.name, "Approved"))
		entry = frappe.db.get_value("Periskope Knowledge Entry", {"parent_entry": cond.name})
		sug = frappe.get_doc("Periskope Suggestion", {"target": entry})
		self.assertIn("New in the tree: Customer is in Bengaluru", sug.reason)
		other = {e.name: e for e in knowledge.find("delivery to chennai")}[self.five.name]
		self.assertIn("Customer is in Bengaluru", other.text)

	def test_a_rep_answering_one_branch_differently_changes_that_branch(self):
		item = self._learned(
			"Delivery time", answer="Chennai delivery now takes 1 day.", place=self.chennai.name
		)
		counts, _prompt = self._mine([item])
		self.assertEqual(counts["changed"], 1)
		sug = frappe.get_doc("Periskope Suggestion", {"kind": "Knowledge Entry", "operation": "Modify"})
		self.assertEqual(sug.target, self.two.name)  # not the Otherwise branch with the same question

	# --- The canvas API -------------------------------------------------------------------------

	def test_graph_save_connect_move_and_remove(self):
		graph = knowledge_canvas.get_graph()
		self.assertEqual(len(graph["nodes"]), 5)
		pricing = knowledge_canvas.save_node(
			json.dumps({"node_type": "Topic", "question": "Pricing", "x": 200})
		)
		self.assertEqual(
			frappe.db.get_value("Periskope Knowledge Entry", pricing["name"], "status"), "Approved"
		)
		gst = knowledge_canvas.save_node(
			{"node_type": "Answer", "question": "Is GST included?", "answer": "Prices exclude GST."}
		)
		self.assertEqual(gst["parent"], "")
		self.assertEqual(knowledge_canvas.connect(gst["name"], pricing["name"])["parent"], pricing["name"])
		with self.assertRaises(frappe.ValidationError):
			knowledge_canvas.connect(self.chennai.name, "")  # a condition cannot start the tree
		knowledge_canvas.move_nodes([{"name": gst["name"], "x": 500, "y": 40}])
		self.assertEqual(
			frappe.db.get_value("Periskope Knowledge Entry", gst["name"], ["pos_x", "pos_y"]), (500, 40)
		)
		self.assertEqual(knowledge_canvas.remove_node(self.delivery.name)["removed"], 5)
		self.assertEqual(
			frappe.db.get_value("Periskope Knowledge Entry", self.five.name, "status"), "Retired"
		)
		self.assertEqual(len(knowledge_canvas.get_graph()["nodes"]), 2)

	def test_an_imported_document_is_saved_only_for_the_picked_answers(self):
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "prices.csv",
				"content": "Item,Price per kg\n30s cotton,245\n40s polyester,180\n",
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		self.assertIn("30s cotton,245", knowledge_canvas.document_text(file.file_url))
		entries = [
			{"question": "Price of 30s cotton?", "answer": "245 per kg.", "keywords": "cotton"},
			{"question": "Price of 40s polyester?", "answer": "180 per kg.", "keywords": "polyester"},
		]
		with (
			patch.object(knowledge_canvas.assistant, "readiness", return_value=(True, "")),
			patch.object(knowledge_canvas.assistant, "complete_json", return_value={"entries": entries}),
		):
			out = knowledge_canvas.import_document(file.file_url)
		self.assertFalse(frappe.db.exists("Periskope Knowledge Entry", {"question": "Price of 30s cotton?"}))
		saved = knowledge_canvas.confirm_import(out["token"], json.dumps([1]), self.delivery.name)["saved"]
		self.assertEqual([s["question"] for s in saved], ["Price of 40s polyester?"])
		self.assertEqual(saved[0]["parent"], self.delivery.name)
		with self.assertRaises(frappe.ValidationError):
			knowledge_canvas.confirm_import(out["token"], "[0]")  # a preview is used once

	def test_no_ai_means_a_plain_message_not_a_call(self):
		with (
			patch.object(knowledge_canvas.assistant, "readiness", return_value=(False, "budget")),
			patch.object(knowledge_canvas.assistant, "complete_json") as call,
		):
			out = knowledge_canvas.import_document("/private/files/none.csv")
		call.assert_not_called()
		self.assertIn("budget", out["error"])

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
			"Do you do custom dyeing for 2000 kg?", [g["question"] for g in knowledge_canvas.gaps()]
		)
		_node("Answer", "Do you do custom dyeing?", None, "Yes, for 500 kg or more.")
		self.assertNotIn(
			"Do you do custom dyeing for 2000 kg?", [g["question"] for g in knowledge_canvas.gaps()]
		)

	def test_only_admins_can_use_it(self):
		original = frappe.session.user
		self.addCleanup(frappe.set_user, original)
		was_testing = frappe.flags.in_test
		frappe.flags.in_test = False
		self.addCleanup(setattr, frappe.flags, "in_test", was_testing)
		frappe.set_user("Guest")
		for call in (
			knowledge_canvas.get_graph,
			lambda: knowledge_canvas.save_node({"node_type": "Topic", "question": "x"}),
			lambda: knowledge_canvas.remove_node(self.delivery.name),
			lambda: knowledge_canvas.confirm_import("x", "[]"),
		):
			with self.assertRaises(frappe.PermissionError):
				call()
