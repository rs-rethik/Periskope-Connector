"""Tests for the Periskope CRM Record doctype and what the agent writes into the CRM (writeback.py,
crm_context.py, ai/analyse.py)."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import answer, chat, crm_context, reminders, safety, writeback
from periskope_connector.ai import analyse
from periskope_connector.tests.utils import isolate, settings

test_ignore = ["CRM Lead", "CRM Deal"]

NUMBER = "919600000432"
CHAT = f"{NUMBER}@c.us"
OTHER = "919600000987@c.us"


class CrmCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_crm_record_test")
		settings(test_mode=1, test_allowed_chats=NUMBER)
		frappe.db.delete("Periskope CRM Record", {"chat_id": ["in", [CHAT, OTHER]]})
		frappe.db.delete("Periskope Chat", {"chat_id": ["in", [CHAT, OTHER]]})
		self.lead = self._lead(NUMBER, "CRM Test")
		chat.ensure(
			frappe._dict(chat_id=CHAT, crm_lead=self.lead, contact_number=NUMBER, direction="Incoming")
		)

	def _lead(self, number, name):
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": name, "mobile_no": f"+{number}", "status": "New"}
		)
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		return lead.name

	def _notes(self):
		return frappe.get_all(
			"FCRM Note", filters={"reference_docname": self.lead}, fields=["name", "title", "content"]
		)

	def _tasks(self, **filters):
		return frappe.get_all(
			"CRM Task",
			filters={"reference_docname": self.lead, **filters},
			fields=["name", "title", "status", "priority", "assigned_to"],
		)


class TestRepText(FrappeTestCase):
	def test_internal_details_never_reach_a_rep(self):
		text = writeback.rep_text(
			"Customer 919600000432@c.us asked (confidence 92%) about KB:abc123 and ITEM:YARN-30 true_9196_3EB0X"
		)
		for bad in ("@c.us", "confidence", "KB:", "ITEM:", "true_"):
			self.assertNotIn(bad, text)
		self.assertIn("Customer", text)
		self.assertNotIn("{", writeback.rep_text('{"wants": "price"}'))


class TestNotesAndTasks(CrmCase):
	def test_one_summary_note_kept_up_to_date(self):
		writeback.summary(CHAT, "Price for 30s yarn.", "Nothing yet.", "Price and stock.")
		writeback.summary(CHAT, "Price for 30s yarn.", "Nothing yet.", "Price and stock.")
		self.assertEqual(len(self._notes()), 1)
		writeback.summary(CHAT, "Price for 30s yarn.", "Price 245 per kg.", "Stock.")
		notes = self._notes()
		self.assertEqual(len(notes), 1)
		self.assertIn("245", notes[0].content)
		self.assertEqual(notes[0].title, "WhatsApp summary")
		self.assertEqual(frappe.db.get_value("Periskope Chat", CHAT, "summary_note"), notes[0].name)

	def test_topic_notes_are_plain_sentences_one_per_topic(self):
		writeback.upsert_note(CHAT, "delivery", "Needs delivery to Tirupur by Friday.")
		writeback.upsert_note(CHAT, "delivery", "Needs delivery to Erode by Monday.")
		writeback.upsert_note(CHAT, "nonsense", "Prefers calls after 5 pm.")
		titles = sorted(n.title for n in self._notes())
		self.assertEqual(titles, ["Delivery", "Other details"])

	def test_one_open_task_per_reason_and_a_new_one_after_done(self):
		first = writeback.upsert_task(CHAT, "reply", "Reply about bulk pricing", "Asked for 2000 kg price.")
		again = writeback.upsert_task(CHAT, "reply", "Reply about bulk pricing today", "Asked again.")
		self.assertEqual(first, again)
		self.assertEqual(len(self._tasks()), 1)
		self.assertEqual(self._tasks()[0].title, "Reply about bulk pricing today")
		frappe.db.set_value("CRM Task", first, "status", "Done")
		third = writeback.upsert_task(CHAT, "reply", "Reply about samples", "Asked for samples.")
		self.assertNotEqual(third, first)
		self.assertEqual(len(self._tasks(status="Todo")), 1)

	def test_a_dry_run_chat_changes_nothing_in_the_crm(self):
		other_lead = self._lead("919600000987", "Other")
		chat.ensure(
			frappe._dict(
				chat_id=OTHER, crm_lead=other_lead, contact_number="919600000987", direction="Incoming"
			)
		)
		frappe.db.delete("Periskope Agent Action", {"chat_id": OTHER})
		self.assertIsNone(writeback.summary(OTHER, "a", "b", "c"))
		self.assertIsNone(writeback.upsert_task(OTHER, "reply", "Reply", "x"))
		self.assertFalse(frappe.get_all("FCRM Note", filters={"reference_docname": other_lead}))
		modes = frappe.get_all("Periskope Agent Action", filters={"chat_id": OTHER}, pluck="mode")
		self.assertEqual(set(modes), {safety.DRY_RUN})

	def test_marking_the_reply_task_done_closes_the_reminder(self):
		reminder = reminders.create_reminder(
			CHAT,
			[
				frappe._dict(
					message_timestamp=now_datetime(),
					contact_number=NUMBER,
					is_group=0,
					name=None,
					body="Price?",
				)
			],
			None,
			reminders.get_config(),
			now_datetime(),
			0.0,
		)
		reminders._create_todo(reminder, 1.0)
		task = frappe.db.get_value("Periskope Reply Reminder", reminder.name, "crm_task")
		self.assertTrue(task)
		doc = frappe.get_doc("CRM Task", task)
		doc.status = "Done"
		doc.save(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Periskope Reply Reminder", reminder.name, "status"), "Closed")


class TestStatus(CrmCase):
	def test_a_clear_step_forward_is_applied(self):
		self.assertEqual(
			writeback.set_status(CHAT, "Contacted", "The customer replied to our quote."), "Applied"
		)
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "status"), "Contacted")

	def test_going_back_is_ignored(self):
		frappe.db.set_value("CRM Lead", self.lead, "status", "Nurture")
		self.assertEqual(writeback.set_status(CHAT, "Contacted", "x"), "")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "status"), "Nurture")

	def test_won_or_lost_is_only_proposed_to_the_owner(self):
		self.assertEqual(writeback.set_status(CHAT, "Junk", "They said they are not interested."), "Proposed")
		self.assertEqual(frappe.db.get_value("CRM Lead", self.lead, "status"), "New")
		self.assertTrue(any("Junk" in t.title for t in self._tasks()))


class TestContextAndAnalysis(CrmCase):
	def test_the_reply_reads_the_crm_but_never_quotes_internal_notes(self):
		writeback.upsert_note(CHAT, "complaint", "Unhappy with the last shade.")
		seen = {}

		def fake(system, user, schema, max_tokens=0):
			seen["user"] = user
			return {"can_answer": False, "answer": "", "confidence": 0, "used_sources": []}

		with (
			patch.object(answer, "gather", return_value=[{"id": "KB:1", "title": "MOQ", "text": "500 kg"}]),
			patch.object(answer.assistant, "complete_json", side_effect=fake),
		):
			answer.build("What is the MOQ?", NUMBER, chat_id=CHAT)
		self.assertIn("CRM CONTEXT (internal, never quote)", seen["user"])
		self.assertIn("Unhappy with the last shade", seen["user"])
		self.assertIn("status New", seen["user"])
		context = crm_context.build(CHAT)
		self.assertEqual(crm_context.customer_sources(context), [])  # a lead has no quotation or stage

	def test_analysis_writes_summary_notes_follow_up_and_status(self):
		reply = {
			"summary": {
				"wants": "Bulk price for 2000 kg.",
				"answered": "Nothing yet.",
				"pending": "The price.",
			},
			"notes": [{"topic": "quantities", "text": "Needs 2000 kg of 30s cotton."}],
			"follow_up": {
				"needed": True,
				"title": "Customer asked for bulk pricing on 30s cotton - call back today",
				"reason": "Large order at stake.",
				"due_in_hours": 3,
				"priority": "High",
			},
			"status": {"change": True, "to": "Contacted", "reason": "The customer is talking to us."},
			"organization": "",
		}
		with (
			patch.object(analyse.assistant, "is_ready", return_value=True),
			patch.object(analyse.assistant, "complete_json", return_value=reply),
		):
			done = analyse.run(CHAT, force=True)
		self.assertTrue(done["summary"])
		self.assertEqual(done["notes"], 1)
		self.assertEqual(done["status"], "Applied")
		tasks = self._tasks()
		self.assertEqual(len(tasks), 1)
		self.assertEqual(tasks[0].priority, "High")
		self.assertIn("bulk pricing", tasks[0].title)

	def test_no_ai_call_without_budget(self):
		with (
			patch.object(analyse.assistant, "is_ready", return_value=False),
			patch.object(analyse.assistant, "complete_json") as call,
		):
			self.assertIn("skipped", analyse.run(CHAT, force=True))
		call.assert_not_called()
