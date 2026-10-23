"""Tests for the Periskope Ai Usage doctype and the code that works on it."""

import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import health, messages
from periskope_connector.ai import classifier
from periskope_connector.api import webhook
from periskope_connector.system import insert_as_system
from periskope_connector.tests.utils import without_site_teams


def _usage(input_tokens=100, output_tokens=20, status="OK", day_offset=0):
	row = insert_as_system(
		{
			"doctype": "Periskope AI Usage",
			"provider": "Direct API",
			"model": "claude-haiku-4-5",
			"input_tokens": input_tokens,
			"output_tokens": output_tokens,
			"status": status,
		}
	)
	if day_offset:
		frappe.db.set_value("Periskope AI Usage", row.name, "creation", add_days(now_datetime(), day_offset))
	return row.name


class TestSpend(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch.object(health, "_manager_emails", lambda: ["boss@example.com"]),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_health_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_health_test")
		without_site_teams(self, "periskope_health_test")
		frappe.db.delete("Periskope AI Usage")  # the site's real usage must not count; rolled back
		frappe.cache().delete_keys("periskope_alert:*")

	def test_month_tokens_counts_this_month_only_and_ignores_errors(self):
		_usage(input_tokens=100, output_tokens=50)
		_usage(input_tokens=1000, status="Error")
		_usage(input_tokens=100, day_offset=-400)  # far in the past: another month entirely
		# only the OK row from the current month counts
		self.assertEqual(health.month_tokens(), 150)

	def test_the_budget_pauses_when_reached(self):
		_usage(input_tokens=600)
		with patch.object(frappe.db, "get_single_value", lambda *a, **k: 1000):
			self.assertFalse(health.budget_exceeded())
		with patch.object(frappe.db, "get_single_value", lambda *a, **k: 500):
			self.assertTrue(health.budget_exceeded())
		with patch.object(frappe.db, "get_single_value", lambda *a, **k: 0):  # 0 = no budget
			self.assertFalse(health.budget_exceeded())

	def test_the_budget_alert_emails_once_per_day(self):
		_usage(input_tokens=600)
		with (
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 500),
			patch.object(frappe, "sendmail") as sendmail,
		):
			self.assertTrue(health.spend_check())
			self.assertFalse(health.spend_check())  # still over, but already alerted today
			self.assertEqual(sendmail.call_count, 1)
			self.assertIn("budget", sendmail.call_args.kwargs["subject"].lower())


class TestCostControls(FrappeTestCase):
	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_cost_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_cost_test")
		without_site_teams(self, "periskope_cost_test")

	def test_a_group_message_gets_more_room_for_its_tasks(self):
		self.assertGreater(classifier.GROUP_MAX_TOKENS, classifier.MAX_TOKENS)

	def test_run_batch_stops_when_the_budget_is_spent(self):
		from periskope_connector import health

		with (
			patch.object(classifier, "_ready_to_run", lambda: True),
			patch.object(health, "budget_exceeded", lambda: True),
			patch.object(classifier, "_pending", side_effect=AssertionError("should not classify")),
		):
			classifier.run_batch()  # must return quietly without touching _pending

	def test_min_length_skips_tiny_messages(self):
		settings = frappe._dict(ai_model="claude-haiku-4-5")
		doc = frappe._dict(direction="Incoming", message_type="text", is_group=0, body="h")
		self.assertEqual(classifier.skip_reason(doc, settings), "Too short to tag")
		doc.body = "hello there"
		self.assertIsNone(classifier.skip_reason(doc, settings))
