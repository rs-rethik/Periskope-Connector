"""Operational health (M10/B5): silent-phone alerts, the AI budget, and the Health figures."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import health
from periskope_connector.system import insert_as_system


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


class TestSyncCheck(FrappeTestCase):
	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch.object(health, "_manager_emails", lambda: ["boss@example.com"]),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_sync_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_sync_test")
		frappe.cache().delete_keys("periskope_alert:*")

	def _phones(self, **columns):
		defaults = {"phone_name": "Sales A", "rep": "Administrator"}
		defaults.update(columns)
		settings = frappe.get_doc("Periskope Settings")
		settings.append("phones", {"phone_id": "health-1", "number": "919600000001", **defaults})
		settings.flags.ignore_permissions = True
		settings.save()

	def test_a_normally_busy_number_that_goes_silent_alerts(self):
		self._phones()
		counts = {"919600000001": SimpleNamespace(today=0, baseline=5.2)}
		with (
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 1),
			patch.object(health, "phone_counts", lambda: counts),
			patch.object(frappe, "sendmail") as sendmail,
		):
			self.assertEqual(health.sync_check(), ["919600000001"])
			self.assertEqual(health.sync_check(), [])  # once a day, not once a run
			self.assertEqual(sendmail.call_count, 1)
			self.assertIn("unlinked", sendmail.call_args.kwargs["subject"].lower())

	def test_a_quiet_number_is_not_a_problem(self):
		self._phones()
		counts = {"919600000001": SimpleNamespace(today=0, baseline=0.3)}
		with (
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 1),
			patch.object(health, "phone_counts", lambda: counts),
			patch.object(frappe, "sendmail"),
		):
			self.assertEqual(health.sync_check(), [])

	def test_a_number_with_traffic_today_is_fine(self):
		self._phones()
		counts = {"919600000001": SimpleNamespace(today=12, baseline=5.2)}
		with (
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 1),
			patch.object(health, "phone_counts", lambda: counts),
			patch.object(frappe, "sendmail"),
		):
			self.assertEqual(health.sync_check(), [])


class TestGetHealth(FrappeTestCase):
	def test_the_figures_come_back_with_the_expected_keys(self):
		figures = health.get_health()
		for key in (
			"last_webhook",
			"last_ai_run",
			"pending",
			"failed",
			"unprocessed",
			"month_tokens",
			"phones",
		):
			self.assertIn(key, figures)


class TestHealthFigures(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("periskope_health_figures_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_health_figures_test")

	def test_the_last_webhook_is_the_newest_log(self):
		frappe.get_doc(
			{
				"doctype": "Periskope Webhook Log",
				"event_type": "message.created",
				"signature_valid": 1,
				"payload": "{}",
			}
		).insert(ignore_permissions=True)
		latest = frappe.db.sql("select max(creation) from `tabPeriskope Webhook Log`")[0][0]
		self.assertEqual(health.get_health()["last_webhook"], str(latest))
		self.assertNotEqual(health.get_health()["last_webhook"], "never")
