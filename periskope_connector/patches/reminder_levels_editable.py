"""The reminder ladder is now set in Periskope Settings > Reminder Ladder. It starts as the ladder the
app always used, so nothing changes until someone edits it. Runs once; a ladder already set is kept."""

import frappe

from periskope_connector.reminders import default_levels


def execute():
	if frappe.get_all("Periskope Reminder Level", filters={"parent": "Periskope Settings"}, limit=1):
		return
	# Added as rows, not by saving the settings: their validation must not stop a migrate.
	for idx, level in enumerate(default_levels(), start=1):
		frappe.get_doc(
			{
				"doctype": "Periskope Reminder Level",
				"parent": "Periskope Settings",
				"parenttype": "Periskope Settings",
				"parentfield": "reminder_levels",
				"idx": idx,
				"wait_minutes": level.wait_minutes,
				"min_severity": level.min_severity,
				"action": level.action,
				"recipients": level.recipients,
			}
		).db_insert()
