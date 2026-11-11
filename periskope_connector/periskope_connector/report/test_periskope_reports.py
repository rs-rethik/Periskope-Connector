"""Reports: every report of the app is installed and runs."""

import frappe
from frappe.desk.query_report import run
from frappe.tests.utils import FrappeTestCase

REPORTS = {
	"Periskope AI Usage",
	"Periskope Lead Source",
	"Periskope Open Escalations",
	"Periskope Response Time",
	"Periskope Sync Health",
	"Periskope Tagging Review",
	"Periskope Unmatched Numbers",
	"Periskope WhatsApp Funnel",
	"Periskope Auto Reply Quality",
}


class TestReports(FrappeTestCase):
	def test_every_report_is_installed(self):
		installed = set(frappe.get_all("Report", filters={"module": "Periskope Connector"}, pluck="name"))
		self.assertEqual(installed, REPORTS)

	def test_every_report_runs_for_a_manager(self):
		for name in sorted(REPORTS):
			result = run(name, filters={})
			self.assertTrue(result.get("columns"), name)
			self.assertIsInstance(result.get("result"), list, name)
