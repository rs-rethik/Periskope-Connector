import re

import frappe

NEW_LINE = "- category is exactly one of the categories listed under Categories below. Pick the one whose description fits the message best.\n"


def execute():
	"""The category list moved out of the saved instructions into the Periskope Tag records, so the
	copy saved in the settings no longer repeats (and contradicts) it."""
	current = frappe.db.get_single_value("Periskope Settings", "ai_instructions") or ""
	updated = re.sub(
		r"- category is exactly one of:\n.*?(?=- Messages labelled)", NEW_LINE, current, count=1, flags=re.S
	)
	if updated != current:
		frappe.db.set_single_value("Periskope Settings", "ai_instructions", updated)
