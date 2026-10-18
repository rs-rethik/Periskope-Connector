"""Tests for the Periskope Suggestion doctype: creating, approving, rejecting and the emailed links."""

import json
import re

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import suggestions
from periskope_connector.tests.utils import isolate, settings


def _make(kind="Tag", operation="Add", target="", payload=None, **kw):
	return suggestions.create(
		kind, operation, kw.pop("summary", "A change"), reason="Because", target=target, payload=payload, **kw
	)


class TestSuggestion(FrappeTestCase):
	def setUp(self):
		self.sent = isolate(self, "periskope_suggestion_test")
		settings(approver_emails="boss@example.com", suggestion_email_gap_hours=0)

	def _token(self, which=0, decision="approve"):
		message = self.sent.call_args.kwargs["message"]
		tokens = re.findall(r"token=([\w\-]+)&decision=" + decision, message)
		return tokens[which]

	def test_nothing_is_applied_when_created(self):
		_make(payload={"tag_name": "bulk_order", "description": "bulk orders"})
		self.assertFalse(frappe.db.exists("Periskope Tag", "bulk_order"))

	def test_same_suggestion_is_not_saved_twice_and_a_no_is_remembered(self):
		first = _make(payload={"tag_name": "bulk_order", "description": "bulk"})
		self.assertTrue(first)
		self.assertIsNone(_make(payload={"tag_name": "bulk_order", "description": "bulk"}))
		suggestions.reject(first, "Desk", "Administrator")
		self.assertIsNone(_make(payload={"tag_name": "bulk_order", "description": "bulk"}))
		self.assertTrue(_make(payload={"tag_name": "other_tag", "description": "x"}))

	def test_payload_is_limited_to_known_fields_and_settings_to_the_allow_list(self):
		doc = _make(payload={"tag_name": "t_one", "description": "d", "api_key": "x"})
		self.assertNotIn("api_key", json.loads(doc.payload))
		self.assertIsNone(_make("Setting", "Modify", target="anthropic_api_key", payload={"value": "x"}))
		self.assertIsNone(_make("Setting", "Modify", target="alert_emails", payload={"value": "a@b.c"}))
		self.assertTrue(_make("Setting", "Modify", target="reply_delay_minutes", payload={"value": 5}))

	def test_approve_adds_a_tag_with_the_usual_actions(self):
		doc = _make(payload={"tag_name": "bulk_order", "description": "bulk orders"})
		self.assertEqual(suggestions.approve(doc, "Desk", "Administrator"), "Applied")
		tag = frappe.get_doc("Periskope Tag", "bulk_order")
		self.assertTrue(tag.active)
		self.assertIn("Email Alert", [a.action for a in tag.actions])
		self.assertEqual(frappe.db.get_value("Periskope Suggestion", doc.name, "status"), "Applied")

	def test_modify_and_remove_a_tag(self):
		tag = frappe.get_all("Periskope Tag", filters={"is_default": 0, "active": 1}, pluck="name")[0]
		doc = _make("Tag", "Modify", target=tag, payload={"description": "New words"})
		suggestions.approve(doc, "Desk", "Administrator")
		self.assertEqual(frappe.db.get_value("Periskope Tag", tag, "description"), "New words")
		doc = _make("Tag", "Remove", target=tag, summary="Retire the tag")
		suggestions.approve(doc, "Desk", "Administrator")
		self.assertEqual(frappe.db.get_value("Periskope Tag", tag, "active"), 0)

	def test_keyword_rule_add_modify_remove(self):
		doc = _make("Keyword Rule", "Add", payload={"rule_name": "Legal", "keywords": "lawyer, notice"})
		suggestions.approve(doc, "Desk", "Administrator")
		rows = {
			r.rule_name: r
			for r in frappe.get_all(
				"Periskope Keyword Rule", fields=["rule_name", "keywords", "enabled", "recipients"]
			)
		}
		self.assertTrue(rows["Legal"].enabled)
		doc = _make("Keyword Rule", "Modify", target="Legal", payload={"keywords": "lawyer, notice, court"})
		suggestions.approve(doc, "Desk", "Administrator")
		rows = {
			r.rule_name: r
			for r in frappe.get_all(
				"Periskope Keyword Rule", fields=["rule_name", "keywords", "enabled", "recipients"]
			)
		}
		self.assertIn("court", rows["Legal"].keywords)
		doc = _make("Keyword Rule", "Remove", target="Legal", summary="Switch off Legal")
		suggestions.approve(doc, "Desk", "Administrator")
		rows = {
			r.rule_name: r
			for r in frappe.get_all(
				"Periskope Keyword Rule", fields=["rule_name", "keywords", "enabled", "recipients"]
			)
		}
		self.assertFalse(rows["Legal"].enabled)

	def test_the_agent_cannot_choose_who_is_emailed(self):
		# Seen with the real AI: it proposed recipients "managers", which broke the alert email.
		settings(alert_emails="alerts@example.com")
		doc = _make(
			"Keyword Rule", "Add", payload={"rule_name": "Moq", "keywords": "moq", "recipients": "managers"}
		)
		self.assertNotIn("recipients", json.loads(doc.payload))
		suggestions.approve(doc, "Desk", "Administrator")
		rows = {
			r.rule_name: r
			for r in frappe.get_all(
				"Periskope Keyword Rule", fields=["rule_name", "keywords", "enabled", "recipients"]
			)
		}
		self.assertEqual(rows["Moq"].recipients, "alerts@example.com")

	def test_sla_level_add_and_remove(self):
		before = frappe.db.count("Periskope SLA Level")
		doc = _make(
			"SLA Level",
			"Add",
			payload={"wait_minutes": 45, "min_severity": "Medium", "action": "Notify Rep"},
		)
		self.assertEqual(suggestions.approve(doc, "Desk", "Administrator"), "Applied")
		self.assertEqual(frappe.db.count("Periskope SLA Level"), before + 1)
		new = frappe.get_all("Periskope SLA Level", filters={"wait_minutes": 45}, fields=["name"])[0]
		doc = _make("SLA Level", "Remove", target=new.name, summary="Drop 45 minute step")
		self.assertEqual(suggestions.approve(doc, "Desk", "Administrator"), "Applied")
		self.assertEqual(frappe.db.count("Periskope SLA Level"), before)

	def test_a_change_that_cannot_be_applied_is_kept_as_failed(self):
		doc = _make("Tag", "Modify", target="no_such_tag", payload={"description": "x"})
		self.assertEqual(suggestions.approve(doc, "Desk", "Administrator"), "Failed")
		row = frappe.db.get_value("Periskope Suggestion", doc.name, ["status", "applied_error"], as_dict=True)
		self.assertEqual(row.status, "Failed")
		self.assertTrue(row.applied_error)

	def test_knowledge_entry_is_used_only_after_approval(self):
		entry = frappe.get_doc(
			{"doctype": "Periskope Knowledge Entry", "question": "Q?", "answer": "A", "status": "Suggested"}
		).insert()
		doc = _make("Knowledge Entry", "Add", target=entry.name, payload={"answer": "A"})
		self.assertEqual(frappe.db.get_value("Periskope Knowledge Entry", entry.name, "status"), "Suggested")
		suggestions.approve(doc, "Desk", "Administrator")
		self.assertEqual(frappe.db.get_value("Periskope Knowledge Entry", entry.name, "status"), "Approved")

	def test_rejected_new_knowledge_entry_is_retired(self):
		entry = frappe.get_doc(
			{"doctype": "Periskope Knowledge Entry", "question": "Q2?", "answer": "A", "status": "Suggested"}
		).insert()
		doc = _make("Knowledge Entry", "Add", target=entry.name, payload={"answer": "A"})
		suggestions.reject(doc, "Desk", "Administrator")
		self.assertEqual(frappe.db.get_value("Periskope Knowledge Entry", entry.name, "status"), "Retired")


class TestEmailedLinks(FrappeTestCase):
	def setUp(self):
		self.sent = isolate(self, "periskope_suggestion_mail_test")
		settings(approver_emails="boss@example.com", suggestion_email_gap_hours=0)
		frappe.db.delete("Periskope Suggestion")
		self.one = _make(payload={"tag_name": "mail_tag_a", "description": "a"})
		self.two = _make(payload={"tag_name": "mail_tag_b", "description": "b"})

	def _tokens(self, decision="approve"):
		return re.findall(r"token=([\w\-]+)&decision=" + decision, self.sent.call_args.kwargs["message"])

	def test_one_email_carries_every_pending_suggestion(self):
		self.assertEqual(suggestions.send_digest(), 2)
		self.assertEqual(self.sent.call_count, 1)
		self.assertEqual(self.sent.call_args.kwargs["recipients"], ["boss@example.com"])
		self.assertEqual(len(self._tokens("approve")), 2)
		self.assertEqual(len(self._tokens("reject")), 2)
		self.assertEqual(suggestions.send_digest(), 0)  # already mailed

	def test_only_the_hash_of_the_token_is_stored(self):
		suggestions.send_digest()
		token = self._tokens()[0]
		stored = frappe.db.get_value("Periskope Suggestion", self.one.name, "token_hash")
		self.assertNotEqual(stored, token)
		self.assertEqual(len(stored), 64)

	def test_approve_link_applies_once(self):
		suggestions.send_digest()
		tokens = self._tokens()
		out = suggestions.decide_by_token(tokens[0], "approve")
		self.assertTrue(out["ok"])
		self.assertTrue(frappe.db.exists("Periskope Tag", "mail_tag_a"))
		again = suggestions.decide_by_token(tokens[0], "approve")
		self.assertFalse(again["ok"])
		self.assertFalse(frappe.db.exists("Periskope Tag", "mail_tag_b"))

	def test_reject_link_changes_nothing(self):
		suggestions.send_digest()
		out = suggestions.decide_by_token(self._tokens("reject")[1], "reject")
		self.assertEqual(out["outcome"], "Rejected")
		self.assertFalse(frappe.db.exists("Periskope Tag", "mail_tag_b"))

	def test_unknown_or_expired_token_does_nothing(self):
		self.assertFalse(suggestions.decide_by_token("nope", "approve")["ok"])
		suggestions.send_digest()
		token = self._tokens()[0]
		frappe.db.set_value("Periskope Suggestion", self.one.name, "expires_on", add_days(now_datetime(), -1))
		self.assertFalse(suggestions.decide_by_token(token, "approve")["ok"])
		self.assertFalse(frappe.db.exists("Periskope Tag", "mail_tag_a"))
		self.assertEqual(frappe.db.get_value("Periskope Suggestion", self.one.name, "status"), "Expired")

	def test_nobody_to_tell_means_nothing_is_marked_as_sent(self):
		settings(approver_emails="", alert_emails="")
		self.assertEqual(suggestions.send_digest(), 0)
		self.assertFalse(frappe.db.get_value("Periskope Suggestion", self.one.name, "emailed_on"))

	def test_confirmation_page_shows_the_change_and_decides_nothing_on_open(self):
		from frappe.website.serve import get_response

		suggestions.send_digest()
		token = self._tokens()[0]
		previous = frappe.session.user
		frappe.set_user("Guest")
		self.addCleanup(frappe.set_user, previous)
		frappe.form_dict.update({"token": token, "decision": "approve"})
		page = get_response("/periskope-suggestion")
		self.assertEqual(page.status_code, 200)
		self.assertIn(b"mail_tag_a", page.data)
		self.assertIn(b'data-decision="approve"', page.data)
		self.assertEqual(frappe.db.get_value("Periskope Suggestion", self.one.name, "status"), "Pending")


class TestNewKinds(FrappeTestCase):
	"""Group mappings, ignored numbers and lead rules, learned and applied through approval."""

	def setUp(self):
		isolate(self, "periskope_new_kinds_test")
		frappe.db.delete("Periskope Group", {"chat_id": "120363999000111@g.us"})
		frappe.get_doc(
			{
				"doctype": "Periskope Group",
				"chat_id": "120363999000111@g.us",
				"group_name": "Sri Murugan Knits Orders",
				"group_type": "Internal",
			}
		).insert(ignore_permissions=True)

	def test_a_group_is_mapped_to_its_company(self):
		doc = _make(
			"Group Mapping",
			"Modify",
			target="120363999000111@g.us",
			payload={"group_type": "Customer", "organization": "Sri Murugan Knits", "crm_owner": "x"},
		)
		self.assertNotIn("crm_owner", json.loads(doc.payload))
		self.assertEqual(suggestions.approve(doc, "Desk", "Administrator"), "Applied")
		group = frappe.db.get_value(
			"Periskope Group",
			{"chat_id": "120363999000111@g.us"},
			["group_type", "organization"],
			as_dict=True,
		)
		self.assertEqual(group.group_type, "Customer")
		self.assertEqual(
			frappe.db.get_value("CRM Organization", group.organization, "organization_name"),
			"Sri Murugan Knits",
		)

	def test_a_number_is_ignored(self):
		doc = _make("Ignore Contact", "Add", target="919600000777", payload={"status": "Ignored (Vendor)"})
		suggestions.approve(doc, "Desk", "Administrator")
		self.assertEqual(
			frappe.db.get_value("Periskope Contact", {"phone": "919600000777"}, "status"), "Ignored (Vendor)"
		)

	def test_a_lead_rule_is_switched_on_and_off(self):
		def has_rule():
			return any(a.action == "Create Lead" for a in frappe.get_doc("Periskope Tag", "general").actions)

		suggestions.approve(
			_make("Lead Rule", "Add", target="general", payload={"create_lead": True}),
			"Desk",
			"Administrator",
		)
		self.assertTrue(has_rule())
		suggestions.approve(
			_make("Lead Rule", "Remove", target="general", summary="Stop leads", payload={}),
			"Desk",
			"Administrator",
		)
		self.assertFalse(has_rule())

	def test_the_learner_only_points_at_what_exists(self):
		from periskope_connector import learner

		config = learner.snapshot()
		config["unmapped_groups"] = [{"chat_id": "120363999000111@g.us", "name": "x", "type": "Internal"}]
		config["unmatched_contacts"] = [{"number": "919600000777", "name": "", "messages": 3, "topic": ""}]
		item = {
			"kind": "Group Mapping",
			"operation": "Modify",
			"target": "120363999000111@g.us",
			"payload_json": "{}",
		}
		self.assertTrue(learner.validate(item, config, True)[0])
		self.assertFalse(learner.validate({**item, "target": "nope@g.us"}, config, True)[0])
		ignore = {
			"kind": "Ignore Contact",
			"operation": "Add",
			"target": "919600000777",
			"payload_json": "{}",
		}
		self.assertTrue(learner.validate(ignore, config, True)[0])
		self.assertFalse(learner.validate({**ignore, "target": "910000000000"}, config, True)[0])
		self.assertFalse(learner.validate(ignore, config, False)[0])  # not enough evidence
