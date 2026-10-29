"""Festival themes are on by default: a site that had Periskope Settings before the switch existed gets
it turned on (a new Check field's default does not reach a Single that is already saved)."""

import frappe


def execute():
	saved = frappe.db.sql(
		"select value from `tabSingles` where doctype='Periskope Settings' and field='festival_themes'"
	)
	if not saved:
		frappe.db.set_single_value("Periskope Settings", "festival_themes", 1)
