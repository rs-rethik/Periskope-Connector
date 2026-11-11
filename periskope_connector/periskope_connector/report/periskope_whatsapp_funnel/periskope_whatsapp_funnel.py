"""What WhatsApp brings in, per lead owner: the leads that came through WhatsApp (source = WhatsApp)
in the period, how many became deals, how many of those deals are won, and the sales orders made
from them (submitted, with their value in the company currency)."""

import frappe
from frappe import _
from frappe.query_builder.functions import Count, Sum
from frappe.utils import add_days, flt, today


def execute(filters=None):
	filters = frappe._dict(filters or {})
	to_date = filters.get("to_date") or today()
	from_date = filters.get("from_date") or add_days(to_date, -30)
	columns = [
		{
			"label": _("Lead Owner"),
			"fieldname": "owner",
			"fieldtype": "Link",
			"options": "User",
			"width": 200,
		},
		{"label": _("WhatsApp Leads"), "fieldname": "leads", "fieldtype": "Int", "width": 130},
		{"label": _("Became Deals"), "fieldname": "deals", "fieldtype": "Int", "width": 120},
		{"label": _("Deals Won"), "fieldname": "won", "fieldtype": "Int", "width": 110},
		{"label": _("Sales Orders"), "fieldname": "orders", "fieldtype": "Int", "width": 120},
		{"label": _("Order Value"), "fieldname": "value", "fieldtype": "Currency", "width": 140},
		{"label": _("Lead → Deal %"), "fieldname": "deal_rate", "fieldtype": "Percent", "width": 120},
	]
	lead = frappe.qb.DocType("CRM Lead")
	leads = (
		frappe.qb.from_(lead)
		.select(lead.name, lead.lead_owner)
		.where(
			(lead.source == "WhatsApp")
			& (lead.creation >= from_date)
			& (lead.creation <= f"{to_date} 23:59:59")
		)
	).run(as_dict=True)
	owner_of = {r.name: r.lead_owner or _("(no owner)") for r in leads}
	rows = {}
	for owner in owner_of.values():
		rows.setdefault(owner, frappe._dict(owner=owner, leads=0, deals=0, won=0, orders=0, value=0.0))
		rows[owner].leads += 1
	if leads:
		won_statuses = frappe.get_all("CRM Deal Status", filters={"type": "Won"}, pluck="name")
		deal = frappe.qb.DocType("CRM Deal")
		deals = (
			frappe.qb.from_(deal)
			.select(deal.name, deal.lead, deal.status)
			.where(deal.lead.isin(list(owner_of)))
		).run(as_dict=True)
		lead_of_deal = {d.name: d.lead for d in deals}
		for d in deals:
			row = rows[owner_of[d.lead]]
			row.deals += 1
			row.won += 1 if d.status in won_statuses else 0
		if deals and frappe.get_meta("Sales Order").has_field("custom_crm_deal"):
			so = frappe.qb.DocType("Sales Order")
			for o in (
				frappe.qb.from_(so)
				.select(so.custom_crm_deal, Count(so.name).as_("n"), Sum(so.base_grand_total).as_("value"))
				.where((so.docstatus == 1) & so.custom_crm_deal.isin(list(lead_of_deal)))
				.groupby(so.custom_crm_deal)
			).run(as_dict=True):
				row = rows[owner_of[lead_of_deal[o.custom_crm_deal]]]
				row.orders += o.n
				row.value += flt(o.value)
	data = sorted(rows.values(), key=lambda r: -r.leads)
	for r in data:
		r.deal_rate = flt(r.deals * 100 / r.leads, 1) if r.leads else 0
	if data:
		total = frappe._dict(
			owner=_("Total"),
			**{k: sum(r[k] for r in data) for k in ("leads", "deals", "won", "orders", "value")},
		)
		total.deal_rate = flt(total.deals * 100 / total.leads, 1) if total.leads else 0
		data.append(total)
	return columns, data
