"""Tests for the Periskope Push Subscription doctype: one row per browser (its endpoint), refreshed when
the same browser subscribes again, and gone when it unsubscribes (push.py)."""

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import push
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES
SUB = {"endpoint": "https://push.example/device-1", "keys": {"p256dh": "key1", "auth": "auth1"}}


class TestPeriskopePushSubscription(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_push_subscription_test")

	def test_one_row_per_browser_refreshed_and_removed(self):
		first = push.subscribe("Administrator", SUB, "Chrome")
		again = push.subscribe(
			"Administrator", {**SUB, "keys": {"p256dh": "key2", "auth": "auth2"}}, "Chrome"
		)
		self.assertEqual(first, again)
		self.assertEqual(frappe.db.get_value("Periskope Push Subscription", first, "p256dh"), "key2")
		push.unsubscribe("Administrator", SUB["endpoint"])
		self.assertFalse(frappe.db.exists("Periskope Push Subscription", first))

	def test_a_browser_without_a_secure_address_or_keys_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			push.subscribe("Administrator", {"endpoint": "http://push.example/x", "keys": SUB["keys"]})
		with self.assertRaises(frappe.ValidationError):
			push.subscribe("Administrator", {"endpoint": SUB["endpoint"], "keys": {}})
