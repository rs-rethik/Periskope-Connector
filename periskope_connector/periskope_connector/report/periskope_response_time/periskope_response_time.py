# Copyright (c) 2026
# For license information, please see license.txt

"""How fast do reps answer? Average and slowest first-response time per rep, in working hours,
from closed Reply Reminders (recorded when the rep's reply arrived)."""

import frappe
from frappe import _
from frappe.utils import add_days, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = filters.get("to_date") or today()
	from_date = filters.get("from_date") or add_days(to_date, -30)

	columns = [
		{"label": _("Rep"), "fieldname": "rep", "fieldtype": "Link", "options": "User", "width": 200},
		{"label": _("Answered Chats"), "fieldname": "chats", "fieldtype": "Int", "width": 120},
		{
			"label": _("Avg First Response (working min)"),
			"fieldname": "avg_mins",
			"fieldtype": "Float",
			"width": 220,
		},
		{"label": _("Slowest (working min)"), "fieldname": "max_mins", "fieldtype": "Float", "width": 160},
		{"label": _("Escalated to L2+"), "fieldname": "escalated", "fieldtype": "Int", "width": 130},
	]

	rows = frappe.db.sql(
		"""select rep,
		count(*) as chats,
		round(avg(nullif(first_response_secs, 0)) / 60, 1) as avg_mins,
		round(max(nullif(first_response_secs, 0)) / 60, 1) as max_mins,
		sum(if(level >= 2, 1, 0)) as escalated
		from `tabPeriskope Reply Reminder`
		where status = 'Closed' and closed_on between %(from)s and %(to)s
		group by rep
		order by avg_mins asc""",
		{"from": from_date, "to": f"{to_date} 23:59:59"},
		as_dict=True,
	)
	return columns, rows
