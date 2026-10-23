"""Tests for the Periskope Tag doctype and the code that works on it."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import install, messages, tags
from periskope_connector.ai import classifier
from periskope_connector.system import insert_as_system
from periskope_connector.tests.utils import event, without_site_teams
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
		without_site_teams(self, "periskope_tags_test")
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


class TestAIDecisions(FrappeTestCase):
	"""What the AI decided is carried out (decisions.py); no rule on a tag decides anything.
	Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: ""),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
			patch("periskope_connector.ai.analyse.queue", lambda *a, **k: None),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_decisions_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_decisions_test")
		without_site_teams(self, "periskope_decisions_test")
		install._ensure_tags()
		frappe.db.set_single_value(
			"Periskope Settings",
			{"test_mode": 0, "alert_emails": "manager@example.com", "escalation_emails": "md@example.com"},
		)
		self.mail = patch.object(frappe, "sendmail").start()
		self.addCleanup(patch.stopall)

	def _message(self, group=False, number="919000000077"):
		chat = "120363000000000001@g.us" if group else f"{number}@c.us"
		return messages.create_message(
			event(message_id=f"ta_{frappe.generate_hash(length=6)}", chat_id=chat, body="where is my order")
		)

	def _decide(self, name, **decisions):
		result = {"category": "delivery_delay", "severity": "Low", "summary": "Late order", **decisions}
		classifier.apply_result(frappe.get_doc("Periskope Message", name), result)
		return frappe.get_doc("Periskope Message", name)

	def test_the_tag_alone_decides_nothing(self):
		with patch("periskope_connector.leads.create_lead_for_message") as lead:
			doc = self._decide(self._message(), severity="High")
		lead.assert_not_called()
		self.mail.assert_not_called()
		self.assertEqual((doc.escalation, doc.on_timeline), ("", 0))

	def test_an_escalation_tells_the_people_the_ai_named_once(self):
		doc = self._decide(self._message(), escalate="managers", escalation_reason="Angry about a late lot")
		self.assertEqual(self.mail.call_args.kwargs["recipients"], ["manager@example.com"])
		self.assertIn("Angry about a late lot", self.mail.call_args.kwargs["subject"])
		self.assertEqual((doc.escalation, doc.high_alert_sent), ("Managers", 1))
		self.mail.reset_mock()
		self._decide(doc.name, escalate="managers", escalation_reason="again")
		self.mail.assert_not_called()  # once per message
		self._decide(self._message(), escalate="md", escalation_reason="Legal notice threatened")
		self.assertEqual(self.mail.call_args.kwargs["recipients"], ["md@example.com", "manager@example.com"])

	def test_a_lead_is_created_when_the_ai_sees_a_new_enquiry(self):
		with patch("periskope_connector.leads.create_lead_for_message") as lead:
			name = self._message()
			self._decide(name, create_lead=True)
			lead.assert_called_once_with(name)
			lead.reset_mock()
			self._decide(self._message(), create_lead=False)
			lead.assert_not_called()

	def test_the_timeline_comment_is_the_ais_call(self):
		from periskope_connector import timeline_comments

		with (
			patch.object(timeline_comments, "_target", lambda d: ("CRM Lead", "X")),
			patch.object(
				timeline_comments, "insert_as_system", return_value=frappe._dict(name="C1")
			) as insert,
		):
			self._decide(self._message(), on_timeline=False)
			self.assertIsNone(timeline_comments.add_comment(self._message()))
			insert.assert_not_called()
			doc = self._decide(self._message(), on_timeline=True)
			self.assertEqual(timeline_comments.add_comment(doc.name), "C1")


class TestDashboardGroups(FrappeTestCase):
	def test_the_starting_tags_are_grouped(self):
		frappe.db.savepoint("periskope_dashboard_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_dashboard_test")
		install._ensure_tags()
		groups = {
			t.name: t.dashboard_group
			for t in frappe.get_all("Periskope Tag", fields=["name", "dashboard_group"])
		}
		self.assertEqual(groups["order_or_enquiry"], "Enquiry")
		self.assertEqual((groups["quality_complaint"], groups["payment_issue"]), ("Issue", "Issue"))
		self.assertFalse(groups["general"])
