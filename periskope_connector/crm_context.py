"""What the CRM knows about a chat, read before the agent replies or writes anything back.

The deal comes first (it is the sales record); a lead is used when there is no deal yet. Two views:
- customer_sources(): facts that may be quoted to the customer (the last quotation, the deal stage).
  They join the answer's DATA, so their figures pass the number check in answer.grounded.
- internal_text(): the rest (owner, notes, open tasks, recent activity), for the agent's own
  judgement only. It is never quoted to the customer.
"""

import re

import frappe
from frappe.utils import cstr, fmt_money, formatdate

MAX_NOTES = 5
MAX_TASKS = 5
MAX_ACTIVITY = 5


def _plain(html: str | None, limit: int = 300) -> str:
	text = re.sub(r"<[^>]+>", " ", html or "")
	return re.sub(r"\s+", " ", text).strip()[:limit]


def record_of(chat_id: str) -> tuple[str, str] | None:
	row = frappe.db.get_value("Periskope Chat", chat_id, ["crm_deal", "crm_lead"], as_dict=True)
	if not row:
		return None
	if row.crm_deal and frappe.db.exists("CRM Deal", row.crm_deal):
		return "CRM Deal", row.crm_deal
	if row.crm_lead and frappe.db.exists("CRM Lead", row.crm_lead):
		return "CRM Lead", row.crm_lead
	return None


def _names(record: tuple[str, str]) -> list[str]:
	"""The record and, for a deal, the lead it came from (their notes and tasks belong together)."""
	doctype, name = record
	names = [name]
	if doctype == "CRM Deal":
		lead = frappe.db.get_value("CRM Deal", name, "lead")
		if lead:
			names.append(lead)
	return names


def _quotation(record: tuple[str, str]) -> frappe._dict | None:
	doctype, name = record
	if doctype != "CRM Deal" or not frappe.db.exists("DocType", "Quotation"):
		return None
	filters = {"docstatus": ["<", 2]}
	if frappe.get_meta("Quotation").has_field("crm_deal"):
		filters["crm_deal"] = name
	else:
		customer = None
		if frappe.get_meta("Customer").has_field("crm_deal"):
			customer = frappe.db.get_value("Customer", {"crm_deal": name})
		if not customer and frappe.get_meta("CRM Deal").has_field("erpnext_customer"):
			customer = frappe.db.get_value("CRM Deal", name, "erpnext_customer")
		if not customer:
			return None
		filters.update(quotation_to="Customer", party_name=customer)
	rows = frappe.get_all(
		"Quotation",
		filters=filters,
		fields=["name", "transaction_date", "valid_till", "grand_total", "currency", "status"],
		order_by="transaction_date desc, creation desc",
		limit=1,
	)
	if not rows:
		return None
	quote = rows[0]
	quote["items"] = frappe.get_all(
		"Quotation Item",
		filters={"parent": quote.name},
		fields=["item_name", "qty", "uom", "rate"],
		order_by="idx asc",
		limit=5,
	)
	return quote


def _record_values(record: tuple[str, str]) -> frappe._dict:
	doctype, name = record
	if doctype == "CRM Deal":
		fields = ["status", "deal_owner as owner", "organization", "deal_value", "currency", "next_step"]
		status_doctype = "CRM Deal Status"
	else:
		fields = ["status", "lead_owner as owner", "organization", "lead_name", "source"]
		status_doctype = "CRM Lead Status"
	meta = frappe.get_meta(doctype)
	fields = [f for f in fields if meta.has_field(f.split(" ")[0])]
	values = frappe.db.get_value(doctype, name, fields, as_dict=True) or frappe._dict()
	values.status_type = frappe.db.get_value(status_doctype, values.status, "type") if values.status else ""
	return values


def build(chat_id: str) -> frappe._dict | None:
	"""Everything the agent reads about the chat's CRM record, or None when it has no record."""
	record = record_of(chat_id)
	if not record:
		return None
	names = _names(record)
	notes = frappe.get_all(
		"FCRM Note",
		filters={"reference_docname": ["in", names]},
		fields=["title", "content", "modified"],
		order_by="modified desc",
		limit=MAX_NOTES,
	)
	tasks = frappe.get_all(
		"CRM Task",
		filters={"reference_docname": ["in", names], "status": ["not in", ["Done", "Canceled"]]},
		fields=["title", "status", "due_date", "assigned_to"],
		order_by="due_date asc",
		limit=MAX_TASKS,
	)
	activity = frappe.get_all(
		"Comment",
		filters={"reference_name": ["in", names], "comment_type": "Comment"},
		fields=["content", "creation"],
		order_by="creation desc",
		limit=MAX_ACTIVITY,
	)
	return frappe._dict(
		doctype=record[0],
		name=record[1],
		info=_record_values(record),
		notes=notes,
		tasks=tasks,
		activity=activity,
		quotation=_quotation(record),
	)


def customer_sources(context) -> list[dict]:
	"""What may be quoted to the customer: the deal stage and the last quotation."""
	if not context:
		return []
	sources = []
	quote = context.quotation
	if quote:
		lines = [
			f"total {fmt_money(quote.grand_total, currency=quote.currency)}",
			f"dated {formatdate(quote.transaction_date)}",
		]
		if quote.valid_till:
			lines.append(f"valid till {formatdate(quote.valid_till)}")
		lines += [f"{i.item_name}: {i.qty:g} {i.uom or ''} at {i.rate:g}" for i in quote["items"]]
		sources.append(
			{"id": f"QUOTE:{quote.name}", "title": "Last quotation sent", "text": ", ".join(lines)}
		)
	if context.doctype == "CRM Deal" and context.info.status:
		sources.append(
			{"id": f"DEAL:{context.name}", "title": "Order stage", "text": cstr(context.info.status)}
		)
	return sources


def internal_text(context) -> str:
	"""The CRM background for the agent (never quoted to the customer)."""
	if not context:
		return "No CRM lead or deal is linked to this chat yet."
	v = context.info
	lines = [
		f"{'Deal' if context.doctype == 'CRM Deal' else 'Lead'}: status {v.status or '-'}"
		f" ({v.status_type or '-'}), owner {v.owner or '-'}, organization {v.organization or '-'}",
	]
	if v.get("next_step"):
		lines.append(f"Next step: {v.next_step}")
	for note in context.notes:
		lines.append(f"Note '{note.title}': {_plain(note.content, 200)}")
	for task in context.tasks:
		lines.append(
			f"Open task: {task.title} (due {task.due_date or '-'}, {task.assigned_to or 'unassigned'})"
		)
	for comment in context.activity:
		lines.append(f"Recent activity: {_plain(comment.content, 150)}")
	if context.quotation:
		lines.append(f"Last quotation {context.quotation.name}: {context.quotation.status}")
	return "\n".join(lines)
