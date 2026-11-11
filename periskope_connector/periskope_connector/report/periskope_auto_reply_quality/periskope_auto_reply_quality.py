"""How the AI's replies to customers went, per rep (the chat's rep): answered by the AI, holding
replies (it could not answer from the data), failed sends, how sure it was, and the share it could
answer itself. A high holding share points at missing answers in the knowledge base."""

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Avg, Count, Sum
from frappe.utils import add_days, flt, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = filters.get("to_date") or today()
	from_date = filters.get("from_date") or add_days(to_date, -30)
	columns = [
		{"label": _("Rep"), "fieldname": "rep", "fieldtype": "Link", "options": "User", "width": 200},
		{"label": _("AI Answered"), "fieldname": "answered", "fieldtype": "Int", "width": 120},
		{"label": _("Holding Replies"), "fieldname": "holding", "fieldtype": "Int", "width": 130},
		{"label": _("Failed"), "fieldname": "failed", "fieldtype": "Int", "width": 90},
		{"label": _("Left to the Rep"), "fieldname": "skipped", "fieldtype": "Int", "width": 120},
		{"label": _("Answered %"), "fieldname": "answered_rate", "fieldtype": "Percent", "width": 110},
		{
			"label": _("Average Sureness"),
			"fieldname": "confidence",
			"fieldtype": "Float",
			"precision": 0,
			"width": 130,
		},
	]
	ar = frappe.qb.DocType("Periskope Auto Reply")

	def count(status):
		return Sum(Case().when(ar.status == status, 1).else_(0))

	rows = (
		frappe.qb.from_(ar)
		.select(
			ar.rep,
			count("Sent").as_("answered"),
			count("Holding Sent").as_("holding"),
			count("Failed").as_("failed"),
			count("Skipped").as_("skipped"),
			Avg(Case().when(ar.status == "Sent", ar.confidence)).as_("confidence"),
			Count(ar.name).as_("n"),
		)
		.where((ar.creation >= from_date) & (ar.creation <= f"{to_date} 23:59:59"))
		.groupby(ar.rep)
	).run(as_dict=True)
	for r in rows:
		r.rep = r.rep or _("(no rep)")
		sent = (r.answered or 0) + (r.holding or 0)
		r.answered_rate = flt((r.answered or 0) * 100 / sent, 1) if sent else 0
		r.confidence = flt(r.confidence or 0, 0)
	rows.sort(key=lambda r: -((r.answered or 0) + (r.holding or 0)))
	return columns, rows
