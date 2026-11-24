# Copyright (c) 2026
# For license information, please see license.txt

"""Every chat currently waiting without an answer: which level it has reached, for whom."""

import frappe
from frappe import _

from periskope_connector import access


def execute(filters=None):
	columns = [
		{
			"label": _("Reminder"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Periskope Reply Reminder",
			"width": 150,
		},
		{"label": _("Level"), "fieldname": "level", "fieldtype": "Int", "width": 70},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 100},
		{"label": _("Rep"), "fieldname": "rep", "fieldtype": "Link", "options": "User", "width": 180},
		{"label": _("Customer"), "fieldname": "contact_number", "fieldtype": "Data", "width": 140},
		{"label": _("Group"), "fieldname": "is_group", "fieldtype": "Check", "width": 70},
		{"label": _("Waiting Since"), "fieldname": "waiting_since", "fieldtype": "Datetime", "width": 160},
		{"label": _("Rep Notified On"), "fieldname": "reminded_on", "fieldtype": "Datetime", "width": 160},
		{
			"label": _("Managers Emailed On"),
			"fieldname": "escalated_on",
			"fieldtype": "Datetime",
			"width": 170,
		},
		{
			"label": _("Latest Unanswered Message"),
			"fieldname": "message_preview",
			"fieldtype": "Data",
			"width": 300,
		},
	]

	rows = frappe.get_all(
		"Periskope Reply Reminder",
		filters={"status": ["in", ["Open", "Escalated"]], **access.chat_id_filter()},
		fields=[
			"name",
			"level",
			"status",
			"rep",
			"contact_number",
			"is_group",
			"waiting_since",
			"reminded_on",
			"escalated_on",
			"message_preview",
		],
		order_by="waiting_since asc",
		limit=500,
	)
	return columns, rows
