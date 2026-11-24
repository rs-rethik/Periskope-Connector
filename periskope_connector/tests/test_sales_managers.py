"""Tests for the Sales Managers listed in Periskope Settings, and for a manager given numbers seeing
only those numbers on the manager pages (dashboard, settings, lists, reports, broadcasts)."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import access, numbers
from periskope_connector.api import admin, broadcast, dashboard, desk_pages, help_bot
from periskope_connector.api.permission import require_manager
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate, make_sales_manager

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = [*OTHER_APP_DOCTYPES, "WhatsApp Number"]

MINE = "919600088801"
OTHER = "919600088802"


def _user(email, *roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": "Manager", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.get_doc("User", email).add_roles(*roles)
	return email


class ManagerCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_sales_managers_test")
		self.addCleanup(frappe.set_user, "Administrator")
		numbers.ensure(MINE)
		numbers.ensure(OTHER)

	def give(self, user, *numbers_):
		frappe.get_doc(
			{
				"doctype": "Periskope Agent",
				"user": user,
				"numbers": [{"whatsapp_number": n} for n in numbers_],
			}
		).insert(ignore_permissions=True)


class TestWhoManages(ManagerCase):
	def test_the_role_alone_does_not_make_a_manager(self):
		role_only = _user("sm.role.only@example.com", "Sales User", "Sales Manager")
		self.assertFalse(access.is_manager(role_only))
		self.assertEqual(access.allowed_numbers(role_only), [])  # a sales user with no numbers
		frappe.set_user(role_only)
		self.assertRaises(frappe.PermissionError, require_manager)
		self.assertTrue(access.can_use_inbox(role_only))  # still works the inbox

	def test_a_listed_user_manages_and_admins_always_do(self):
		listed = make_sales_manager(_user("sm.listed@example.com", "Sales User"))
		self.assertTrue(access.is_manager(listed))
		self.assertIsNone(access.allowed_numbers(listed))  # no numbers given: every number
		self.assertTrue(access.is_manager(_user("sm.admin@example.com", "System Manager")))

	def test_the_list_is_saved_from_the_settings_page(self):
		listed = _user("sm.saved@example.com", "Sales User")
		admin.save_settings("team", {"sales_managers": [listed]})
		self.assertTrue(access.is_manager(listed))
		team = next(s for s in admin.get_settings()["sections"] if s["key"] == "team")
		field = next(f for f in team["fields"] if f["fieldname"] == "sales_managers")
		self.assertEqual(field["value"], [listed])


class TestLimitedManager(ManagerCase):
	def setUp(self):
		super().setUp()
		self.manager = make_sales_manager(_user("sm.limited@example.com", "Sales User"))
		self.give(self.manager, MINE)
		self.other_agent = _user("sm.other.agent@example.com", "Sales User")
		self.give(self.other_agent, OTHER)

	def test_settings_show_and_change_only_their_numbers(self):
		frappe.set_user(self.manager)
		self.assertEqual({r["name"] for r in admin.list_records("numbers")}, {MINE})
		self.assertNotIn(self.other_agent, {r["user"] for r in admin.list_records("agents")})
		self.assertEqual({p["value"] for p in admin.pick("WhatsApp Number")}, {MINE})
		self_agent = frappe.db.get_value("Periskope Agent", {"user": self.manager})
		self.assertRaises(  # cannot give themselves a number they do not work
			frappe.PermissionError,
			admin.save_record,
			"agents",
			{"numbers": [MINE, OTHER]},
			self_agent,
		)

	def test_the_dashboard_shows_only_their_numbers(self):
		frappe.set_user(self.manager)
		out = dashboard.get_dashboard()
		self.assertEqual({n["name"] for n in out["numbers"]}, {MINE})
		self.assertNotIn(self.other_agent, {r["value"] for r in out["reps"]})
		self.assertRaises(frappe.PermissionError, dashboard.get_dashboard, number=OTHER)

	def test_lists_broadcasts_and_reports_follow_their_numbers(self):
		frappe.get_doc(
			{"doctype": "Periskope Group", "chat_id": "120363888000555@g.us", "group_type": "Internal"}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Periskope Chat",
				"chat_id": "120363888000555@g.us",
				"is_group": 1,
				"whatsapp_number": OTHER,
				"org_phone": OTHER,
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		frappe.set_user(self.manager)
		rows = desk_pages.list_rows("groups", json.dumps({}))["rows"]
		self.assertNotIn("120363888000555@g.us", [r.get("chat_id") for r in rows])
		self.assertRaises(frappe.PermissionError, desk_pages.list_rows, "webhook_logs")
		self.assertRaises(frappe.PermissionError, broadcast.recipients, OTHER)
		self.assertNotIn("Periskope AI Usage", help_bot.allowed_reports())
