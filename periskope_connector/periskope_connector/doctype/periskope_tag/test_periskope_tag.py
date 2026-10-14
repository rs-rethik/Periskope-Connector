"""Tests for the Periskope Tag doctype and the code that works on it."""

from datetime import datetime, timezone
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import actions, dashboard, install, messages, tags
from periskope_connector.ai import classifier
from periskope_connector.system import insert_as_system
from periskope_connector.tests.utils import event
from periskope_connector.text_rules import skip_fields


def _tag(name, description="a test tag", **kwargs):
	doc = frappe.get_doc({"doctype": "Periskope Tag", "tag_name": name, "description": description, **kwargs})
	doc.insert(ignore_permissions=True)
	return doc


class TestTags(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_tags_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_tags_test")
		install._ensure_tags()

	def test_the_starting_set_is_created_once(self):
		self.assertEqual(set(tags.names()), {name for name, _ in tags.DEFAULT_TAGS} | set(tags.names()))
		before = frappe.db.count("Periskope Tag")
		install._ensure_tags()
		self.assertEqual(frappe.db.count("Periskope Tag"), before)
		self.assertEqual(tags.default_tag(), "general")

	def test_a_new_tag_reaches_the_prompt_the_schema_and_the_reply_format(self):
		_tag("zz_negotiation", "the customer pushes back on the price")
		self.assertIn(
			"zz_negotiation: the customer pushes back on the price", classifier.build_system_prompt()
		)
		self.assertIn("zz_negotiation", classifier.build_schema()["properties"]["category"]["enum"])
		self.assertIn('"zz_negotiation"', classifier.json_reply_instructions())

	def test_an_inactive_tag_is_no_longer_offered(self):
		frappe.db.set_value("Periskope Tag", "sample_request", "active", 0)
		self.assertNotIn("sample_request", tags.names())
		self.assertNotIn("sample_request", classifier.build_system_prompt())

	def test_an_unknown_answer_from_the_ai_falls_back_to_the_default_tag(self):
		doc = insert_as_system({"doctype": "Periskope Message", "message_id": "tag_fallback", "body": "hi"})
		classifier.apply_result(doc, {"category": "made_up", "severity": "Low", "sentiment": "Neutral"})
		self.assertEqual(doc.ai_category, "general")
		frappe.db.set_value("Periskope Tag", "general", "is_default", 0)
		frappe.db.set_value("Periskope Tag", "delivery_delay", "is_default", 1)
		classifier.apply_result(doc, {"category": "made_up"})
		self.assertEqual(doc.ai_category, "delivery_delay")

	def test_skipped_messages_get_the_default_tag(self):
		frappe.db.set_value("Periskope Tag", "general", "is_default", 0)
		frappe.db.set_value("Periskope Tag", "delivery_delay", "is_default", 1)
		self.assertEqual(skip_fields("Too short to tag")["ai_category"], "delivery_delay")

	def test_the_name_must_be_lowercase_with_underscores(self):
		for bad in ("Bad Name", "1abc", "UPPER", "with-dash"):
			with self.assertRaises(frappe.ValidationError, msg=bad):
				_tag(bad)

	def test_only_one_tag_is_the_default(self):
		_tag("new_default", is_default=1)
		self.assertEqual(frappe.db.count("Periskope Tag", {"is_default": 1}), 1)
		self.assertEqual(tags.default_tag(), "new_default")

	def test_the_default_tag_cannot_be_deactivated_or_deleted(self):
		general = frappe.get_doc("Periskope Tag", "general")
		general.active = 0
		with self.assertRaises(frappe.ValidationError):
			general.save()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Periskope Tag", "general")

	def test_the_last_active_tag_cannot_be_deactivated(self):
		for name in tags.names():
			if name != "general":
				frappe.db.set_value("Periskope Tag", name, "active", 0)
		general = frappe.get_doc("Periskope Tag", "general")
		general.active = 0
		with self.assertRaises(frappe.ValidationError):
			general.save()

	def test_a_manager_can_only_correct_to_an_active_tag(self):
		from periskope_connector.api import settings as settings_api

		frappe.db.set_value("Periskope Tag", "sample_request", "active", 0)
		with self.assertRaises(frappe.ValidationError):
			settings_api.set_correct_category("any", "sample_request")


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
			event(message_id=f"ta_{frappe.generate_hash(length=6)}", chat_id=chat, body="where is my order")
		)
		frappe.db.set_value(
			"Periskope Message", name, {"ai_category": tag, "severity": severity, "ai_summary": "Late order"}
		)
		return frappe.get_doc("Periskope Message", name)

	def test_actions_for_a_message_that_does_not_exist_do_nothing(self):
		actions.run("no-such-message")
		self.assertIsNone(actions.create_rep_todo("no-such-message"))
		self.mail.assert_not_called()

	def test_the_starting_actions_match_the_old_behaviour(self):
		# Start from an empty table, as on a new site: the team's own edits on this site must not count.
		frappe.db.delete("Periskope Tag Action")
		frappe.db.delete("Periskope Tag")
		install._ensure_tags()

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


class TestDashboardGroups(FrappeTestCase):
	"""The workspace cards count the tags in a dashboard group, not fixed tag names."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: ""),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_dashboard_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_dashboard_test")
		install._ensure_tags()

	def _message(self, tag, message_id):
		now = datetime.now(timezone.utc).isoformat()
		name = messages.create_message(
			event(message_id=message_id, chat_id="919000000088@c.us", timestamp=now)
		)
		frappe.db.set_value("Periskope Message", name, "ai_category", tag)
		return name

	def test_the_starting_tags_are_grouped(self):
		groups = {
			t.name: t.dashboard_group
			for t in frappe.get_all("Periskope Tag", fields=["name", "dashboard_group"])
		}
		self.assertEqual(groups["order_or_enquiry"], "Enquiry")
		self.assertEqual((groups["quality_complaint"], groups["payment_issue"]), ("Issue", "Issue"))
		self.assertFalse(groups["general"])

	def test_a_card_counts_the_recent_messages_of_the_tags_in_its_group(self):
		before = dashboard.issues()["value"]
		self._message("quality_complaint", "dg_1")
		self._message("general", "dg_2")  # no group: not counted
		self.assertEqual(dashboard.issues()["value"], before + 1)

	def test_a_tag_moved_into_a_group_is_counted_at_once(self):
		before = dashboard.enquiries()["value"]
		self._message("sample_request", "dg_3")
		self.assertEqual(dashboard.enquiries()["value"], before)
		recent = frappe.db.count(
			"Periskope Message",
			{
				"ai_category": "sample_request",
				"direction": "Incoming",
				"message_timestamp": [">=", add_days(now_datetime(), -dashboard.DAYS)],
			},
		)  # includes the one above, and any the site already has
		frappe.db.set_value("Periskope Tag", "sample_request", "dashboard_group", "Enquiry")
		self.assertEqual(dashboard.enquiries()["value"], before + recent)

	def test_the_card_opens_the_list_filtered_to_the_group(self):
		result = dashboard.issues()
		self.assertEqual(result["route"], ["List", "Periskope Message"])
		self.assertIn("quality_complaint", result["route_options"]["ai_category"][1])
