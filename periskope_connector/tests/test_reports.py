"""Reports: every report of the app is installed and runs, and every workspace link to one resolves."""

import json

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

	def test_every_workspace_report_link_points_to_an_installed_report(self):
		workspace = frappe.get_doc("Workspace", "Periskope")
		for shortcut in workspace.shortcuts:
			if shortcut.type == "Report":
				self.assertTrue(frappe.db.exists("Report", shortcut.link_to), shortcut.link_to)
		for block in json.loads(workspace.content):
			self.assertIn("type", block)
