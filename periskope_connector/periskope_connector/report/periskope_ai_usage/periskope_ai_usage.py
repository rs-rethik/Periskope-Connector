# Copyright (c) 2026
# For license information, please see license.txt

"""What the AI tagging costs: calls, tokens and cache hits per day and model, from the usage log."""

import frappe
from frappe import _
from frappe.utils import add_days, today

from periskope_connector import access


def execute(filters=None):
	access.require_every_number()  # the AI's cost covers every number
	filters = frappe._dict(filters or {})
	to_date = filters.get("to_date") or today()
	from_date = filters.get("from_date") or add_days(to_date, -30)

	columns = [
		{"label": _("Day"), "fieldname": "day", "fieldtype": "Date", "width": 110},
		{"label": _("Model"), "fieldname": "model", "fieldtype": "Data", "width": 160},
		{"label": _("Calls"), "fieldname": "calls", "fieldtype": "Int", "width": 80},
		{"label": _("Input Tokens"), "fieldname": "input_tokens", "fieldtype": "Int", "width": 120},
		{"label": _("Output Tokens"), "fieldname": "output_tokens", "fieldtype": "Int", "width": 120},
		{"label": _("Cache Read"), "fieldname": "cache_read_tokens", "fieldtype": "Int", "width": 110},
		{"label": _("Cache Write"), "fieldname": "cache_creation_tokens", "fieldtype": "Int", "width": 110},
		{"label": _("Total Tokens"), "fieldname": "total", "fieldtype": "Int", "width": 110},
	]

	rows = frappe.db.sql(
		"""select date(creation) as day, model, count(*) as calls,
		sum(input_tokens) as input_tokens, sum(output_tokens) as output_tokens,
		sum(cache_read_tokens) as cache_read_tokens, sum(cache_creation_tokens) as cache_creation_tokens,
		sum(input_tokens + output_tokens + cache_read_tokens + cache_creation_tokens) as total
		from `tabPeriskope AI Usage`
		where creation between %(from)s and %(to)s
		group by day, model
		order by day desc, model""",
		{"from": from_date, "to": f"{to_date} 23:59:59"},
		as_dict=True,
	)
	return columns, rows
