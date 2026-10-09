"""Tag actions: what happens to a message is decided by the rows on its tag."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import actions, install, messages, tags
from periskope_connector.tests.test_linking import _event


class TestTagActions(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: ""),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_actions_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_actions_test")
		install._ensure_tags()
		self.mail = patch.object(frappe, "sendmail").start()
		self.addCleanup(patch.stopall)

	def _tag_with(self, rows, name="delivery_delay"):
		tag = frappe.get_doc("Periskope Tag", name)
		tag.set("actions", [])
		for row in rows:
			tag.append("actions", row)
		tag.save()

	def _message(self, tag="delivery_delay", severity="Low", group=False):
		chat = "120363000000000001@g.us" if group else "919000000077@c.us"
		name = messages.create_message(
			_event(message_id=f"ta_{frappe.generate_hash(length=6)}", chat_id=chat, body="where is my order")
		)
		frappe.db.set_value(
			"Periskope Message", name, {"ai_category": tag, "severity": severity, "ai_summary": "Late order"}
		)
		return frappe.get_doc("Periskope Message", name)

	def test_the_starting_actions_match_the_old_behaviour(self):
		def rows(tag):
			return {
				(r.action, r.min_severity, r.chat_type) for r in frappe.get_doc("Periskope Tag", tag).actions
			}

		self.assertEqual(
			rows("order_or_enquiry"),
			{
				("Email Alert", "High", "Any"),
				("Timeline Comment", "Low", "Any"),
				("Create Lead", "Low", "1-to-1"),
			},
		)
		self.assertEqual(rows("general"), {("Email Alert", "High", "Any")})
		self.assertEqual(
			rows("payment_issue"), {("Email Alert", "High", "Any"), ("Timeline Comment", "Low", "Any")}
		)

	def test_a_row_applies_from_its_minimum_severity_up(self):
		self._tag_with([{"action": "Email Alert", "min_severity": "Medium", "chat_type": "Any"}])
		for severity, expected in (("Low", 0), ("Medium", 1), ("High", 1)):
			doc = self._message(severity=severity)
			self.assertEqual(len(actions.matching(doc, actions.EMAIL_ALERT)), expected, severity)

	def test_a_row_can_be_limited_to_groups_or_one_to_one_chats(self):
		self._tag_with(
			[
				{"action": "Timeline Comment", "min_severity": "Low", "chat_type": "Group"},
				{"action": "Email Alert", "min_severity": "Low", "chat_type": "1-to-1"},
			]
		)
		single, group = self._message(), self._message(group=True)
		self.assertEqual(len(actions.matching(single, actions.TIMELINE_COMMENT)), 0)
		self.assertEqual(len(actions.matching(group, actions.TIMELINE_COMMENT)), 1)
		self.assertEqual(len(actions.matching(single, actions.EMAIL_ALERT)), 1)
		self.assertEqual(len(actions.matching(group, actions.EMAIL_ALERT)), 0)

	def test_a_tag_without_rows_only_labels_the_message(self):
		self._tag_with([])
		with patch("periskope_connector.leads.create_lead_for_message") as lead:
			actions.run(self._message(severity="High").name)
		lead.assert_not_called()
		self.mail.assert_not_called()

	def test_a_high_severity_message_is_emailed_through_the_tag_row(self):
		self._tag_with(
			[
				{
					"action": "Email Alert",
					"min_severity": "High",
					"chat_type": "Any",
					"recipients": "qa@example.com",
				}
			]
		)
		actions.run(self._message(severity="High").name)
		self.assertEqual(self.mail.call_args.kwargs["recipients"], ["qa@example.com"])
		self.mail.reset_mock()
		actions.run(self._message(severity="Medium").name)
		self.mail.assert_not_called()

	def test_a_lead_is_only_created_when_the_tag_says_so(self):
		with patch("periskope_connector.leads.create_lead_for_message") as lead:
			self._tag_with([{"action": "Create Lead", "min_severity": "Low", "chat_type": "Any"}])
			name = self._message().name
			actions.run(name)
			lead.assert_called_once_with(name)
			lead.reset_mock()
			self._tag_with([])
			actions.run(self._message().name)
			lead.assert_not_called()

	def test_a_todo_for_the_rep_is_created_once(self):
		self._tag_with([{"action": "ToDo for Rep", "min_severity": "Low", "chat_type": "Any"}])
		doc = self._message(severity="High")
		first = actions.create_rep_todo(doc.name)
		self.assertTrue(first)
		self.assertIsNone(actions.create_rep_todo(doc.name))
		todo = frappe.get_doc("ToDo", first)
		self.assertEqual((todo.allocated_to, todo.priority), ("Administrator", "High"))

	def test_no_todo_without_a_rep(self):
		self._tag_with([{"action": "ToDo for Rep", "min_severity": "Low", "chat_type": "Any"}])
		doc = self._message()
		frappe.db.set_value("Periskope Message", doc.name, "rep", None)
		self.assertIsNone(actions.create_rep_todo(doc.name))

	def test_the_timeline_comment_follows_the_tag_rows(self):
		from periskope_connector import timeline_comments

		with patch.object(timeline_comments, "_target", lambda d: ("CRM Lead", "X")):
			doc = self._message()
			self._tag_with([])
			self.assertIsNone(timeline_comments.add_comment(doc.name))
		self.assertIn("general", tags.names())
