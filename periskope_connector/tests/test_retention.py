"""Retention (M6): raw payloads and (optionally) bodies are cleared after the configured days."""

from datetime import timedelta
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import retention
from periskope_connector.system import insert_as_system


def _message(message_id, days_old=0, body="hello", raw='{"data": {}}'):
	name = insert_as_system(
		{
			"doctype": "Periskope Message",
			"message_id": message_id,
			"body": body,
			"raw_payload": raw,
		}
	).name
	if days_old:
		old = now_datetime() - timedelta(days=days_old)
		frappe.db.set_value("Periskope Message", name, {"creation": old, "modified": old})
	return name


class TestRetention(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_retention_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_retention_test")

	def test_raw_payloads_older_than_the_setting_are_cleared(self):
		old, fresh = _message("ret1", days_old=100), _message("ret2")
		cleared = retention.clear("Periskope Message", "raw_payload", 90)
		self.assertGreaterEqual(cleared, 1)
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "raw_payload"), "")
		self.assertEqual(frappe.db.get_value("Periskope Message", fresh, "raw_payload"), '{"data": {}}')

	def test_zero_days_keeps_everything(self):
		old = _message("ret3", days_old=400)
		self.assertEqual(retention.clear("Periskope Message", "raw_payload", 0), 0)
		self.assertTrue(frappe.db.get_value("Periskope Message", old, "raw_payload"))

	def test_bodies_are_only_cleared_when_configured(self):
		old = _message("ret4", days_old=100)
		with patch.object(frappe, "get_cached_doc") as cached:
			cached.return_value = frappe._dict(raw_payload_retention_days=90, body_retention_days=0)
			retention.run()
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "raw_payload"), "")
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "body"), "hello")

		with patch.object(frappe, "get_cached_doc") as cached:
			cached.return_value = frappe._dict(raw_payload_retention_days=90, body_retention_days=90)
			retention.run()
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "body"), "")
