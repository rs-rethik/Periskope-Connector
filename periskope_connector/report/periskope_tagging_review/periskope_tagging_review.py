# Copyright (c) 2026
# For license information, please see license.txt

"""AI tagging review: what was tagged, what the sales team corrected, and where the AI struggles.

Rows are classified messages (newest first). To correct a category, open the message and use
Correct Category — the correction feeds back into the AI prompt the same day.
"""

import frappe
from frappe import _
from frappe.utils import add_days, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = filters.get("to_date") or today()
	from_date = filters.get("from_date") or add_days(to_date, -7)

	columns = [
		{
			"label": _("Message"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Periskope Message",
			"width": 160,
		},
		{"label": _("When"), "fieldname": "message_timestamp", "fieldtype": "Datetime", "width": 150},
		{"label": _("Customer"), "fieldname": "contact_number", "fieldtype": "Data", "width": 140},
		{"label": _("Text"), "fieldname": "body", "fieldtype": "Data", "width": 320},
		{"label": _("AI Category"), "fieldname": "ai_category", "fieldtype": "Data", "width": 140},
		{"label": _("Severity"), "fieldname": "severity", "fieldtype": "Data", "width": 90},
		{"label": _("Corrected To"), "fieldname": "correct_category", "fieldtype": "Data", "width": 140},
		{"label": _("Rep"), "fieldname": "rep", "fieldtype": "Link", "options": "User", "width": 150},
	]

	rows = frappe.get_all(
		"Periskope Message",
		filters={
			"classification_status": "Done",
			"classified_at": ["between", [from_date, f"{to_date} 23:59:59"]],
		},
		fields=[
			"name",
			"message_timestamp",
			"contact_number",
			"body",
			"ai_category",
			"severity",
			"correct_category",
			"rep",
		],
		order_by="classified_at desc",
		limit=1000,
	)
	for row in rows:
		row.body = (row.body or "")[:160]

	corrected = [r for r in rows if r.correct_category]
	correction_rate = f"{len(corrected)} / {len(rows)}" if rows else "0 / 0"
	rows.append(
		frappe._dict(
			{
				"name": "",
				"message_timestamp": None,
				"contact_number": _("Corrections (total): {0}").format(len(corrected)),
				"body": _("Correction rate: {0}").format(correction_rate),
				"ai_category": "",
				"severity": "",
				"correct_category": "",
				"rep": "",
			}
		)
	)
	return columns, rows
