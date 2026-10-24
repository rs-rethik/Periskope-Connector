"""Tests for the inbox's Admin Settings (phase e): who may open which page, teams and agents edited
from the inbox, the settings sections with write-only keys, and the number health alert."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import numbers
from periskope_connector.api import admin
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

A = "919600011111"


def _user(email, *roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.get_doc("User", email).add_roles(*roles)
	return email


class AdminCase(FrappeTestCase):
	def setUp(self):
		from frappe.cache_manager import clear_doctype_map

		self.addCleanup(clear_doctype_map, "Assignment Rule", "Periskope Chat")
		self.mail = isolate(self, "periskope_admin_test")
		self.addCleanup(frappe.set_user, "Administrator")
		numbers.ensure(A)
		self.rep = _user("admin.rep@example.com", "Sales User")
		self.manager = _user("admin.manager@example.com", "Sales Manager")


class TestWhoMayOpenWhat(AdminCase):
	def test_sales_users_get_no_settings(self):
		frappe.set_user(self.rep)
		for call in (
			lambda: admin.get_meta("teams"),
			lambda: admin.list_records("agents"),
			lambda: admin.save_record("teams", {"team_name": "X"}),
			lambda: admin.get_settings(),
			lambda: admin.pick("User"),
		):
			self.assertRaises(frappe.PermissionError, call)

	def test_managers_run_the_team_but_not_the_system(self):
		frappe.set_user(self.manager)
		self.assertTrue(admin.get_meta("teams")["fields"])
		self.assertEqual(admin.my_access(), {"manager": True, "admin": False})
		self.assertRaises(frappe.PermissionError, admin.get_meta, "labels")
		self.assertRaises(frappe.PermissionError, admin.get_settings)
		self.assertRaises(frappe.PermissionError, admin.run_action, "test_connection")

	def test_only_listed_doctypes_can_be_picked(self):
		self.assertRaises(frappe.ValidationError, admin.pick, "Periskope Settings")
		users = [u.value for u in admin.pick("User")]
		self.assertNotIn("Administrator", users)


class TestRecords(AdminCase):
	def test_a_team_is_made_and_changed_from_the_inbox(self):
		frappe.set_user(self.manager)
		team = admin.save_record(
			"teams",
			{
				"team_name": "Sales Desk",
				"assignment": "Round Robin",
				"members": [self.rep, self.manager],
				"numbers": [A],
			},
		)
		self.assertEqual((team["members"], team["numbers"]), ([self.rep, self.manager], [A]))
		rule = frappe.get_doc(
			"Assignment Rule", frappe.db.get_value("Periskope Team", "Sales Desk", "assignment_rule")
		)
		self.assertEqual(sorted(u.user for u in rule.users), sorted([self.rep, self.manager]))
		changed = admin.save_record(
			"teams", {"team_name": "Renamed?", "members": [self.rep]}, name="Sales Desk"
		)
		self.assertEqual(
			(changed["name"], changed["team_name"], changed["members"]),
			("Sales Desk", "Sales Desk", [self.rep]),
		)
		self.assertEqual([u.user for u in frappe.get_doc("Assignment Rule", rule.name).users], [self.rep])
		admin.delete_record("teams", "Sales Desk")
		self.assertFalse(frappe.db.exists("Assignment Rule", rule.name))

	def test_numbers_come_from_periskope_and_only_their_setup_changes(self):
		self.assertRaises(frappe.ValidationError, admin.save_record, "numbers", {"display_name": "New"})
		row = admin.save_record("numbers", {"display_name": "Sales desk", "wa_state": "HACKED"}, name=A)
		self.assertEqual(row["display_name"], "Sales desk")
		self.assertNotEqual(frappe.db.get_value("WhatsApp Number", A, "wa_state"), "HACKED")
		self.assertRaises(frappe.ValidationError, admin.delete_record, "numbers", A)

	def test_inviting_an_agent_makes_a_sales_user_on_the_numbers(self):
		frappe.set_user(self.manager)
		agent = admin.invite_agent("New.Rep@Example.com", "New Rep", [A])
		self.assertEqual((agent["user"], agent["numbers"]), ("new.rep@example.com", [A]))
		self.assertIn("Sales User", frappe.get_roles("new.rep@example.com"))
		self.assertEqual(frappe.db.get_value("User", "new.rep@example.com", "send_welcome_email"), 0)
		self.assertRaises(frappe.ValidationError, admin.invite_agent, "not an email", "x")


class TestSettings(AdminCase):
	def test_keys_are_never_sent_and_kept_when_left_empty(self):
		doc = frappe.get_single("Periskope Settings")
		doc.api_key = "secret-key-123"
		doc.flags.ignore_permissions = True
		doc.save()
		data = admin.get_settings()
		api_key = next(f for s in data["sections"] for f in s["fields"] if f["fieldname"] == "api_key")
		self.assertEqual((api_key["value"], api_key["is_set"]), ("", True))
		self.assertNotIn("secret-key-123", frappe.as_json(data))
		admin.save_settings("connect", {"api_key": "", "connection_status": "Changed?"})
		settings_doc = frappe.get_single("Periskope Settings")
		self.assertEqual(settings_doc.get_password("api_key"), "secret-key-123")
		self.assertNotEqual(settings_doc.connection_status, "Changed?")  # read-only stays as it was
		admin.save_settings("connect", {"api_key": "new-key-456"})
		self.assertEqual(frappe.get_single("Periskope Settings").get_password("api_key"), "new-key-456")

	def test_a_section_saves_only_its_own_fields(self):
		admin.save_settings(
			"test_mode",
			{"test_mode": 1, "test_allowed_chats": "916374955805", "enable_auto_reply": 1},
		)
		self.assertEqual(
			frappe.db.get_single_value("Periskope Settings", "test_allowed_chats"), "916374955805"
		)
		self.assertEqual(frappe.db.get_single_value("Periskope Settings", "test_mode"), 1)
		section = next(s for s in admin.get_settings()["sections"] if s["key"] == "test_mode")
		self.assertEqual(
			[f["fieldname"] for f in section["fields"]],
			["test_mode", "test_allowed_chats", "test_allowed_emails"],
		)

	def test_unknown_actions_and_sections_are_refused(self):
		self.assertRaises(frappe.ValidationError, admin.run_action, "drop_database")
		self.assertRaises(frappe.ValidationError, admin.save_settings, "everything", {})


class TestNumberHealth(AdminCase):
	def test_a_number_that_drops_alerts_the_managers_once(self):
		settings(enabled=1)
		frappe.db.set_value("WhatsApp Number", A, {"is_ready": 1, "wa_state": "CONNECTED"})
		frappe.get_doc(
			{"doctype": "Periskope Chat", "chat_id": "919600099991@c.us", "whatsapp_number": A}
		).insert(ignore_permissions=True)
		dropped = [{"phone_id": "p1", "org_phone": f"{A}@c.us", "wa_state": "TIMEOUT", "is_ready": False}]
		with (
			patch.object(frappe.utils.password, "get_decrypted_password", lambda *a, **k: "key"),
			patch("periskope_connector.api.settings._fetch_phones", lambda: dropped),
		):
			self.assertIn(A, numbers.health_check())  # (the site's own numbers are not in the list either)
			self.assertEqual(numbers.health_check(), [])  # still down: not again
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": self.manager, "type": "Alert"}))
		self.assertTrue(frappe.db.get_value("WhatsApp Number", A, "disconnected_since"))
