"""Test mode used to hold every email of the site; it now holds only this app's own emails unless
"Hold Every Email of the Site" is on. A site already in test mode keeps the old behaviour (it may be a
copy of a live site whose other apps must not email real people)."""

import frappe


def execute():
	if frappe.db.get_single_value("Periskope Settings", "test_mode"):
		frappe.db.set_single_value("Periskope Settings", "test_hold_all_emails", 1)
