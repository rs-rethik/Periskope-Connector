# Copyright (c) 2026
# For license information, please see license.txt

"""Is every linked WhatsApp number actually delivering messages? Today's count per company
number against its own 14-day average — a sudden zero means the phone unlinked from Periskope."""

import frappe
from frappe import _

from periskope_connector.health import phone_counts


def execute(filters=None):
	columns = [
		{"label": _("Number"), "fieldname": "number", "fieldtype": "Data", "width": 150},
		{"label": _("Phone"), "fieldname": "phone_name", "fieldtype": "Data", "width": 150},
		{"label": _("Rep"), "fieldname": "rep", "fieldtype": "Link", "options": "User", "width": 180},
		{"label": _("State"), "fieldname": "wa_state", "fieldtype": "Data", "width": 110},
		{"label": _("Messages (24 h)"), "fieldname": "today", "fieldtype": "Int", "width": 130},
		{"label": _("Daily Average (14 d)"), "fieldname": "baseline", "fieldtype": "Float", "width": 160},
		{"label": _("Healthy"), "fieldname": "healthy", "fieldtype": "Data", "width": 90},
	]

	from periskope_connector.health import MIN_DAILY_TRAFFIC

	counts = phone_counts()
	rows = []
	for phone in frappe.get_all(
		"Periskope Phone",
		filters={"parent": "Periskope Settings"},
		fields=["number", "phone_name", "rep", "wa_state"],
		order_by="number asc",
	):
		stats = counts.get(phone.number) or frappe._dict(today=0, baseline=0)
		healthy = _("Yes") if stats.today > 0 or stats.baseline < MIN_DAILY_TRAFFIC else _("SILENT")
		rows.append(
			frappe._dict(
				number=phone.number,
				phone_name=phone.phone_name,
				rep=phone.rep,
				wa_state=phone.wa_state,
				today=stats.today,
				baseline=stats.baseline,
				healthy=healthy,
			)
		)
	return columns, rows
