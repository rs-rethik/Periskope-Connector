# Copyright (c) 2026
# For license information, please see license.txt

"""Leads that came in through WhatsApp: how many per rep per week (source = WhatsApp, the value
this app sets on every lead it creates)."""

import frappe
from frappe import _
from frappe.utils import add_days, today

from periskope_connector import access


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = filters.get("to_date") or today()
	from_date = filters.get("from_date") or add_days(to_date, -28)

	columns = [
		{"label": _("Week Starting"), "fieldname": "week", "fieldtype": "Data", "width": 120},
		{
			"label": _("Lead Owner"),
			"fieldname": "lead_owner",
			"fieldtype": "Link",
			"options": "User",
			"width": 200,
		},
		{"label": _("WhatsApp Leads"), "fieldname": "leads", "fieldtype": "Int", "width": 140},
	]

	leads = access.leads_on_my_numbers()  # None: every number
	# raw SQL: the week is worked out with MariaDB's date functions, which qb cannot group by
	rows = frappe.db.sql(
		"""select date_sub(date(creation), interval weekday(date(creation)) day) as week,
		ifnull(lead_owner, '(no owner)') as lead_owner,
		count(*) as leads
		from `tabCRM Lead`
		where source = 'WhatsApp' and creation between %(from)s and %(to)s
		and (%(every)s or name in %(leads)s)
		group by week, lead_owner
		order by week desc, leads desc""",
		{
			"from": from_date,
			"to": f"{to_date} 23:59:59",
			"leads": tuple(leads or [""]),
			"every": int(leads is None),
		},
		as_dict=True,
	)
	for row in rows:
		row.week = str(row.week)
	return columns, rows
