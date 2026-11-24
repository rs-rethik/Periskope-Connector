# Copyright (c) 2026
# For license information, please see license.txt

"""How fast do reps answer? Average and slowest first-response time per rep, in working hours,
from closed Reply Reminders (recorded when the rep's reply arrived)."""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Avg, Count, Max, Round, Sum
from frappe.utils import add_days, today
from pypika.functions import NullIf

from periskope_connector import access


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

	r = frappe.qb.DocType("Periskope Reply Reminder")
	secs = NullIf(r.first_response_secs, 0)
	query = (
		frappe.qb.from_(r)
		.select(
			r.rep,
			Count("*").as_("chats"),
			Round(Avg(secs) / 60, 1).as_("avg_mins"),
			Round(Max(secs) / 60, 1).as_("max_mins"),
			Sum(Case().when(r.level >= 2, 1).else_(0)).as_("escalated"),
		)
		.where((r.status == "Closed") & r.closed_on.between(from_date, f"{to_date} 23:59:59"))
		.groupby(r.rep)
		.orderby("avg_mins")
	)
	allowed = access.allowed_numbers()
	if allowed is not None:  # a manager given numbers: the chats on their numbers only
		query = query.where(r.chat_id.isin(access.chat_ids_on(allowed) or [""]))
	return columns, query.run(as_dict=True)
