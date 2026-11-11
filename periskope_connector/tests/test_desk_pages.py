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

		# saving only the type keeps the customer; marking it internal clears it
		desk_pages.map_group(group.name, group_type="Customer")
		self.assertEqual(frappe.db.get_value("Periskope Group", group.name, "crm_lead"), lead.name)
		desk_pages.map_group(group.name, group_type="Internal")
		self.assertIsNone(frappe.db.get_value("Periskope Group", group.name, "crm_lead"))

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


class TestDeskRetired(DeskPagesCase):
	def test_the_desk_screens_are_removed_and_the_doctypes_stay(self):
		from periskope_connector.patches import retire_desk_screens

		frappe.get_doc(
			{"doctype": "Custom HTML Block", "__newname": retire_desk_screens.BLOCK, "html": "<div></div>"}
		).insert(ignore_permissions=True)
		for label in ("Messages Today", "Holding Messages Today"):  # a current and an older card name
			frappe.get_doc(
				{
					"doctype": "Number Card",
					"label": label,
					"document_type": "Periskope Message",
					"function": "Count",
					"module": retire_desk_screens.MODULE,
				}
			).insert(ignore_permissions=True)
		other = frappe.get_doc(
			{"doctype": "Number Card", "label": "Not Ours", "document_type": "ToDo", "function": "Count"}
		).insert(ignore_permissions=True)
		retire_desk_screens.execute()
		for doctype, name in (
			("Workspace", retire_desk_screens.WORKSPACE),
			("Custom HTML Block", retire_desk_screens.BLOCK),
			("Number Card", "Messages Today"),
			("Number Card", "Holding Messages Today"),
			("Page", retire_desk_screens.PAGE),
		):
			self.assertFalse(frappe.db.exists(doctype, name), name)
		self.assertTrue(frappe.db.exists("Number Card", other.name))
		for doctype in ("Periskope Settings", "Periskope Message", "Periskope Chat", "Periskope Contact"):
			self.assertTrue(frappe.db.exists("DocType", doctype), doctype)
		retire_desk_screens.execute()  # runs again without error
