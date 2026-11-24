"""The Desk screens of the app, served to the WhatsApp inbox so managers never need Desk: the data
lists (contact queue, groups, auto replies, reminders, the agent's log, AI usage, webhook logs), the
learning agent's suggestions. (The reports page was taken out of the app for now; the Periskope
reports are still in Desk.)

Everything here is read-only except what the screens offered in Desk: a contact can be made a lead,
linked or ignored (api/contacts.py), a group mapped to a lead, deal or organization, and a suggestion
approved or rejected (suggestions.decide). Managers only.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint

from periskope_connector import access
from periskope_connector.api.permission import require_manager

PAGE = 50

# The lists, with the columns they show, the filters they offer and how they are sorted.
LISTS = {
	"contacts": {
		"doctype": "Periskope Contact",
		"title": "Contact queue",
		"fields": [
			"name",
			"status",
			"phone",
			"display_name",
			"message_count",
			"last_message_at",
			"linked_lead",
			"linked_deal",
			"reps",
		],
		"filters": ["status"],
		"search": ["phone", "display_name"],
		"order_by": "last_message_at desc",
	},
	"groups": {
		"doctype": "Periskope Group",
		"title": "Groups",
		"fields": ["name", "group_name", "group_type", "crm_lead", "crm_deal", "organization"],
		"filters": ["group_type"],
		"search": ["group_name", "name"],
		"order_by": "modified desc",
	},
	"auto_replies": {
		"doctype": "Periskope Auto Reply",
		"title": "Auto replies",
		"fields": [
			"name",
			"status",
			"contact_number",
			"due_on",
			"sent_on",
			"confidence",
			"customer_text",
			"reply_text",
			"cancel_reason",
			"error",
		],
		"filters": ["status"],
		"search": ["contact_number", "customer_text", "reply_text"],
		"order_by": "creation desc",
	},
	"reminders": {
		"doctype": "Periskope Reply Reminder",
		"title": "Reply reminders",
		"fields": ["name", "status", "rep", "contact_number", "waiting_since", "level", "message_preview"],
		"filters": ["status", "level"],
		"search": ["contact_number", "message_preview"],
		"order_by": "creation desc",
	},
	"agent_actions": {
		"doctype": "Periskope Agent Action",
		"title": "What the agent did",
		"fields": ["name", "creation", "action", "mode", "origin", "chat_id", "summary", "error"],
		"filters": ["action", "mode", "origin"],
		"search": ["summary", "chat_id"],
		"order_by": "creation desc",
	},
	"agent_runs": {
		"doctype": "Periskope Agent Run",
		"title": "Agent runs",
		"fields": ["name", "status", "started_on", "knowledge_added", "suggestions_added"],
		"filters": ["status"],
		"search": [],
		"order_by": "creation desc",
	},
	"ai_usage": {
		"doctype": "Periskope AI Usage",
		"title": "AI usage",
		"fields": ["name", "creation", "model", "input_tokens", "output_tokens", "status"],
		"filters": ["model", "status"],
		"search": [],
		"order_by": "creation desc",
	},
	"webhook_logs": {
		"doctype": "Periskope Webhook Log",
		"title": "Webhook logs",
		"fields": ["name", "creation", "event_type", "signature_valid"],
		"filters": ["event_type", "signature_valid"],
		"search": [],
		"order_by": "creation desc",
	},
	"suggestions": {
		"doctype": "Periskope Suggestion",
		"title": "Suggestions",
		"fields": ["name", "creation", "kind", "operation", "target", "status", "summary", "reason"],
		"filters": ["kind", "operation", "status"],
		"search": ["summary", "target"],
		"order_by": "creation desc",
	},
}


def _manager():
	access.require_inbox_user()
	require_manager()
	if not access.is_manager():
		frappe.throw(_("This page is for managers."), frappe.PermissionError)


def _list(kind: str) -> dict:
	cfg = LISTS.get(kind)
	if not cfg:
		frappe.throw(_("Unknown list {0}").format(kind))
	return cfg


@frappe.whitelist()
def list_meta(kind: str) -> dict:
	"""The columns and filter choices of a list (labels and options from the doctype)."""
	access.require_inbox_user()
	_manager()
	cfg = _list(kind)
	if kind not in NUMBER_SCOPE and access.allowed_numbers() is not None:
		access.require_every_number()
	meta = frappe.get_meta(cfg["doctype"])

	def describe(name):
		if name in ("name", "creation"):
			return {
				"fieldname": name,
				"label": _("ID") if name == "name" else _("Created"),
				"fieldtype": "Data" if name == "name" else "Datetime",
			}
		df = meta.get_field(name)
		return {"fieldname": name, "label": _(df.label), "fieldtype": df.fieldtype, "options": df.options}

	return {
		"title": _(cfg["title"]),
		"doctype": cfg["doctype"],
		"columns": [describe(f) for f in cfg["fields"]],
		"filters": [describe(f) for f in cfg["filters"]],
		"searchable": bool(cfg["search"]),
	}


# How a list is kept to the numbers of a manager who was given numbers: the field that holds the chat
# id, or the contact's number. The others are app-wide and need someone who sees every number.
NUMBER_SCOPE = {
	"contacts": "phone",
	"groups": "chat_id",
	"auto_replies": "chat_id",
	"reminders": "chat_id",
	"agent_actions": "chat_id",
}


def _scope(kind: str) -> dict:
	"""The extra filter that keeps a list to the user's numbers ({} when they see every number)."""
	allowed = access.allowed_numbers()
	if allowed is None:
		return {}
	field = NUMBER_SCOPE.get(kind)
	if not field:
		access.require_every_number()
	chat_ids = access.chat_ids_on(allowed)
	if field == "phone":
		values = [c.split("@")[0] for c in chat_ids if c.endswith("@c.us")]
	else:
		values = chat_ids
	return {field: ["in", values or [""]]}


@frappe.whitelist()
def list_rows(
	kind: str, filters: str | dict | None = None, search: str = "", start: int = 0, page_length: int = 0
) -> dict:
	"""A page of a list; page_length (at most 500) lets the page reload the rows it already shows."""
	access.require_inbox_user()
	_manager()
	cfg = _list(kind)
	filters = json.loads(filters) if isinstance(filters, str) else (filters or {})
	where = {f: v for f, v in filters.items() if f in cfg["filters"] and v not in (None, "")}
	where.update(_scope(kind))
	or_filters = (
		[[f, "like", f"%{search.strip()}%"] for f in cfg["search"]] if (search or "").strip() else None
	)
	rows = frappe.get_all(
		cfg["doctype"],
		filters=where,
		or_filters=or_filters,
		fields=cfg["fields"],
		order_by=cfg["order_by"],
		start=cint(start),
		page_length=min(cint(page_length) or PAGE, 500),
	)
	total = len(
		frappe.get_all(cfg["doctype"], filters=where, or_filters=or_filters, pluck="name", limit=5000)
	)
	return {"rows": rows, "total": total}


# --- What the Desk screens let you do -------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def contact_action(
	contact: str, action: str, reason: str = "", lead: str | None = None, deal: str | None = None
):
	"""The contact queue's buttons (api/contacts.py keeps the work and its own checks)."""
	access.require_inbox_user()
	_manager()
	from periskope_connector.api import contacts

	if action == "create_lead":
		return contacts.create_lead(contact)
	if action == "ignore":
		return contacts.ignore(contact, reason or "Ignored (Personal)")
	if action == "link":
		return contacts.link_to(contact, lead=lead, deal=deal)
	if action == "matches":
		return contacts.find_matches(contact)
	frappe.throw(_("Unknown action {0}").format(action))


@frappe.whitelist(methods=["POST"])
def map_group(
	group: str,
	group_type: str | None = None,
	crm_lead: str | None = None,
	crm_deal: str | None = None,
	organization: str | None = None,
) -> dict:
	"""Map a WhatsApp group to its customer (a lead, a deal or an organization), or mark it internal.
	A new customer replaces the old one; an internal group has none; saving only the type keeps the
	customer it has. Its chats in the inbox show the customer and its messages are linked again."""
	access.require_inbox_user()
	_manager()
	doc = frappe.get_doc("Periskope Group", group)
	access.require_chat_id(doc.chat_id)
	if organization and not (crm_lead or crm_deal):
		# a company: the group follows its best deal or lead, like linking it from the chat
		from periskope_connector.api.crm_panel import set_group_organization

		set_group_organization(doc.chat_id, doc.name, organization, frappe.session.user)
		return frappe.get_doc("Periskope Group", group).as_dict()
	if group_type == "Internal" and not (crm_lead or crm_deal):
		# a team group: marked so it stays one, and taken out of the CRM (like the chat's button)
		from periskope_connector import groups

		groups.mark_internal(doc.chat_id, frappe.session.user)
		return frappe.get_doc("Periskope Group", group).as_dict()
	if group_type:
		doc.group_type = group_type
	if doc.group_type == "Customer":
		doc.marked_internal = 0
	if crm_lead or crm_deal or organization or doc.group_type == "Internal":
		doc.crm_lead = crm_lead or None
		doc.crm_deal = crm_deal or None
		doc.organization = organization or None
	doc.flags.ignore_permissions = True
	doc.save()
	from periskope_connector.linking import relink_group

	# the same as linking it from the chat: the chat rows follow, the messages are linked again
	relink_group(doc.name, frappe.session.user)
	return doc.as_dict()


@frappe.whitelist(methods=["POST"])
def decide_suggestion(name: str, decision: str) -> str:
	"""Approve or reject one of the learning agent's suggestions, as the emailed links do."""
	access.require_inbox_user()
	_manager()
	access.require_every_number()  # a suggestion changes the app for every number
	from periskope_connector.suggestions import decide

	return decide(name, decision)
