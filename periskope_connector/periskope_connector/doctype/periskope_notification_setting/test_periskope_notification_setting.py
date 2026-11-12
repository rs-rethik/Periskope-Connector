"""Tests for the Periskope Notification Setting doctype: one row per user, made and changed through the
inbox (api/notifications.save_preferences)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector.api import notifications
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


class TestPeriskopeNotificationSetting(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_notification_setting_test")
		frappe.db.delete("Periskope Notification Setting", {"user": "Administrator"})

	def test_the_users_own_row_is_made_once_and_then_changed(self):
		notifications.save_preferences(0, "21:00", "08:00")
		notifications.save_preferences(1, "22:00", "07:00")
		rows = frappe.get_all(
			"Periskope Notification Setting",
			filters={"user": "Administrator"},
			fields=["push_off", "quiet_from", "quiet_to"],
		)
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].push_off, str(rows[0].quiet_from)[:5]), (1, "22:00"))

	def test_the_user_is_unique(self):
		frappe.get_doc({"doctype": "Periskope Notification Setting", "user": "Administrator"}).insert()
		with self.assertRaises(frappe.DuplicateEntryError):
			frappe.get_doc({"doctype": "Periskope Notification Setting", "user": "Administrator"}).insert()
