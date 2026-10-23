"""Tests for the Desk screens moved into the WhatsApp inbox (phase g): the data lists, the contact
queue and group actions, the suggestions and the reports, all for managers only."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector.api import desk_pages
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


class DeskPagesCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_desk_pages_test")
		self.addCleanup(frappe.set_user, "Administrator")
		self.rep = "pages.rep@example.com"
		if not frappe.db.exists("User", self.rep):
			frappe.get_doc(
				{"doctype": "User", "email": self.rep, "first_name": "Rep", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", self.rep).add_roles("Sales User")


class TestLists(DeskPagesCase):
	def test_sales_users_get_none_of_it(self):
		frappe.set_user(self.rep)
		for call in (
			lambda: desk_pages.list_meta("contacts"),
			lambda: desk_pages.list_rows("agent_actions"),
			lambda: desk_pages.reports(),
			lambda: desk_pages.run_report("Periskope AI Usage"),
			lambda: desk_pages.decide_suggestion("x", "approve"),
		):
			self.assertRaises(frappe.PermissionError, call)

	def test_every_list_reads_with_its_filters_and_search(self):
		for kind in desk_pages.LISTS:
			meta = desk_pages.list_meta(kind)
			self.assertTrue(meta["columns"], kind)
			self.assertIn("rows", desk_pages.list_rows(kind), kind)
		frappe.get_doc(
			{
				"doctype": "Periskope Contact",
				"phone": "+919600090001",
				"display_name": "Pages Buyer",
				"status": "New",
			}
		).insert(ignore_permissions=True)
		found = desk_pages.list_rows("contacts", {"status": "New", "not_a_filter": "x"}, search="Pages Buyer")
		self.assertEqual([r.display_name for r in found["rows"]], ["Pages Buyer"])
		self.assertRaises(frappe.ValidationError, desk_pages.list_meta, "users")

	def test_the_contact_queue_ignores_and_groups_map_to_a_customer(self):
		contact = frappe.get_doc(
			{
				"doctype": "Periskope Contact",
				"phone": "+919600090002",
				"display_name": "Spammer",
				"status": "New",
			}
		).insert(ignore_permissions=True)
		desk_pages.contact_action(contact.name, "ignore", reason="Ignored (Spam)")
		self.assertEqual(frappe.db.get_value("Periskope Contact", contact.name, "status"), "Ignored (Spam)")
		self.assertRaises(frappe.ValidationError, desk_pages.contact_action, contact.name, "explode")

		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Group owner"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		group = frappe.get_doc(
			{
				"doctype": "Periskope Group",
				"chat_id": "120363999990001@g.us",
				"group_name": "Buyers",
				"group_type": "Internal",
			}
		).insert(ignore_permissions=True)
		with patch("periskope_connector.linking.link_message") as relink:
			desk_pages.map_group(group.name, group_type="Customer", crm_lead=lead.name)
		self.assertEqual(
			frappe.db.get_value("Periskope Group", group.name, ["group_type", "crm_lead"]),
			("Customer", lead.name),
		)
		self.assertEqual(relink.call_count, 0)  # no messages in that group yet

	def test_a_suggestion_is_decided_once(self):
		doc = frappe.get_doc(
			{
				"doctype": "Periskope Suggestion",
				"kind": "Tag",
				"operation": "Add",
				"target": "pages_test_tag",
				"summary": "A tag",
				"status": "Pending",
				"payload": frappe.as_json({"tag_name": "pages_test_tag", "description": "x"}),
			}
		).insert(ignore_permissions=True)
		desk_pages.decide_suggestion(doc.name, "reject")
		self.assertEqual(frappe.db.get_value("Periskope Suggestion", doc.name, "status"), "Rejected")
		self.assertRaises(frappe.ValidationError, desk_pages.decide_suggestion, doc.name, "approve")


class TestReports(DeskPagesCase):
	def test_every_report_runs_and_unknown_ones_are_refused(self):
		names = [r["name"] for r in desk_pages.reports()]
		self.assertEqual(sorted(names), sorted(desk_pages.REPORTS))
		for name in names:
			out = desk_pages.run_report(name, {"from_date": "2026-10-01", "to_date": "2026-10-23"})
			self.assertTrue(out["columns"], name)
		self.assertRaises(frappe.PermissionError, desk_pages.run_report, "Stock Balance")
