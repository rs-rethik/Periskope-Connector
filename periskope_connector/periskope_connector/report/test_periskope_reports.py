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

	def test_everything_the_workspace_shows_exists(self):
		workspace = frappe.get_doc("Workspace", "Periskope")
		blocks = [b for b in json.loads(workspace.content) if b["type"] != "spacer"]
		cards = {c.number_card_name for c in workspace.number_cards}
		charts = {c.chart_name for c in workspace.charts}
		shortcuts = {s.label: s for s in workspace.shortcuts}
		cards_in_page = [b["data"]["number_card_name"] for b in blocks if b["type"] == "number_card"]
		charts_in_page = [b["data"]["chart_name"] for b in blocks if b["type"] == "chart"]
		shortcuts_in_page = [b["data"]["shortcut_name"] for b in blocks if b["type"] == "shortcut"]
		self.assertTrue(cards_in_page and charts_in_page and shortcuts_in_page)
		for name in cards_in_page:
			self.assertIn(name, cards, name)
			self.assertTrue(frappe.db.exists("Number Card", name), name)
		for name in charts_in_page:
			self.assertIn(name, charts, name)
			self.assertTrue(frappe.db.exists("Dashboard Chart", name), name)
		for label in shortcuts_in_page:
			self.assertIn(label, shortcuts, label)
			shortcut = shortcuts[label]
			if shortcut.type == "URL":
				self.assertTrue(shortcut.url.startswith("/app/periskope-settings"), label)
			else:
				self.assertTrue(
					frappe.db.exists(shortcut.type.replace("Report", "Report"), shortcut.link_to), label
				)

	def test_the_flow_shortcut_opens_the_settings_with_the_flow_dialog(self):
		workspace = frappe.get_doc("Workspace", "Periskope")
		flow = next(s for s in workspace.shortcuts if s.label == "Flow")
		self.assertEqual((flow.type, flow.url), ("URL", "/app/periskope-settings?flow=1"))
