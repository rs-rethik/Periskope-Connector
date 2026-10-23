"""The CRM panel beside a chat in the WhatsApp inbox: the lead or deal of the chat, and what a rep
does with it without opening the CRM (link or create the lead, add a note or task, change the status).

What the panel shows needs access to the chat (access.py). What it changes in the CRM is done as the
user, with the CRM's own permissions: a rep can only touch the leads and deals the CRM lets them.
"""

import re

import frappe
from frappe import _
from frappe.utils import strip_html

from periskope_connector import access, chat, numbers
from periskope_connector.linking import link_message

DOCTYPES = ("CRM Lead", "CRM Deal")
NOTE_LIMIT = 5
CHAT_FIELDS = [
	"name",
	"chat_id",
	"is_group",
	"contact_number",
	"chat_name",
	"crm_lead",
	"crm_deal",
	"summary_note",
	"linked_by_hand",
]


def _chat(name: str) -> frappe._dict:
	access.require_inbox_user()
	if not access.can_see_chat(name):
		frappe.throw(_("You cannot open this chat."), frappe.PermissionError)
	return frappe.db.get_value("Periskope Chat", name, CHAT_FIELDS, as_dict=True)


def _record(row) -> tuple[str, str] | None:
	if row.crm_deal and frappe.db.exists("CRM Deal", row.crm_deal):
		return "CRM Deal", row.crm_deal
	if row.crm_lead and frappe.db.exists("CRM Lead", row.crm_lead):
		return "CRM Lead", row.crm_lead
	return None


def _route(doctype: str, name: str) -> str:
	return f"/crm/{'deals' if doctype == 'CRM Deal' else 'leads'}/{name}"


def statuses(doctype: str) -> list[dict]:
	status_doctype, field = (
		("CRM Deal Status", "deal_status") if doctype == "CRM Deal" else ("CRM Lead Status", "lead_status")
	)
	return [
		{"value": r[field], "color": r.color, "type": r.type}
		for r in frappe.get_all(status_doctype, fields=[field, "color", "type"], order_by="position asc")
	]


@frappe.whitelist()
def get_context(chat_name: str) -> dict:
	"""The lead or deal of a chat with its status, owner, value, the WhatsApp summary, recent notes,
	open tasks and AI tags, plus the contact's chats on our other numbers."""
	access.require_inbox_user()
	row = _chat(chat_name)
	out = {
		"chat": chat_name,
		"is_group": row.is_group,
		"linked_by_hand": row.linked_by_hand,
		"record": None,
		"tags": _tags(chat_name),
		"other_chats": _other_chats(row),
	}
	record = _record(row)
	if not record:
		return out
	doctype, name = record
	if not frappe.has_permission(doctype, "read", name):
		out["record"] = {"doctype": doctype, "name": name, "hidden": True}
		return out
	doc = frappe.get_doc(doctype, name)
	owner = doc.get("deal_owner" if doctype == "CRM Deal" else "lead_owner")
	out["record"] = {
		"doctype": doctype,
		"name": name,
		"title": (doc.get("organization") if doctype == "CRM Deal" else None) or doc.get("lead_name") or name,
		"status": doc.status,
		"statuses": statuses(doctype),
		"owner": owner,
		"owner_name": frappe.utils.get_fullname(owner) if owner else "",
		"value": doc.get("deal_value") or doc.get("expected_deal_value") or doc.get("annual_revenue") or 0,
		"currency": doc.get("currency") or frappe.db.get_default("currency"),
		"organization": doc.get("organization"),
		"email": doc.get("email"),
		"mobile_no": doc.get("mobile_no"),
		"modified": doc.modified,
		"route": _route(doctype, name),
		"can_edit": bool(frappe.has_permission(doctype, "write", name)),
		"summary": _summary(row.summary_note),
		"notes": [n for n in _notes(doctype, name) if n.name != row.summary_note],
		"tasks": _tasks(doctype, name),
	}
	return out


def _summary(note: str | None) -> str:
	"""The AI's WhatsApp summary note as plain text, one line per heading or paragraph."""
	if not note or not frappe.db.exists("FCRM Note", note):
		return ""
	html = frappe.db.get_value("FCRM Note", note, "content") or ""
	html = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6])>", "\n", html)
	return re.sub(r"\n{3,}", "\n\n", strip_html(html)).strip()


def _notes(doctype: str, name: str) -> list[dict]:
	if not frappe.has_permission("FCRM Note", "read"):
		return []
	return frappe.get_list(
		"FCRM Note",
		filters={"reference_doctype": doctype, "reference_docname": name},
		fields=["name", "title", "content", "owner", "creation"],
		order_by="modified desc",
		limit=NOTE_LIMIT,
	)


def _tasks(doctype: str, name: str) -> list[dict]:
	if not frappe.has_permission("CRM Task", "read"):
		return []
	return frappe.get_list(
		"CRM Task",
		filters={
			"reference_doctype": doctype,
			"reference_docname": name,
			"status": ["not in", ["Done", "Canceled"]],
		},
		fields=["name", "title", "status", "priority", "due_date", "assigned_to"],
		order_by="due_date asc",
		limit=20,
	)


def _tags(chat_name: str) -> list[dict]:
	"""The AI's tags on the customer's messages in this chat, most frequent first."""
	return [
		{"tag": tag, "count": count}
		for tag, count in frappe.db.sql(
			"""select ai_category, count(*) from `tabPeriskope Message`
			where periskope_chat = %s and direction = 'Incoming' and ifnull(ai_category, '') != ''
			group by ai_category order by count(*) desc limit 6""",
			(chat_name,),
		)
	]


def _other_chats(row) -> list[dict]:
	"""The same contact on our other numbers, where the user may see them."""
	if row.is_group:
		return []
	others = frappe.get_all(
		"Periskope Chat",
		filters={"chat_id": row.chat_id, "name": ["!=", row.name]},
		fields=["name", "whatsapp_number", "last_message_at", "last_message_preview"],
		order_by="last_message_at desc",
	)
	return [
		{**o, "number_label": numbers.label(o.whatsapp_number)} for o in others if access.can_see_chat(o.name)
	]


# --- Linking ------------------------------------------------------------------------------------


@frappe.whitelist()
def search_records(query: str = "") -> list[dict]:
	"""Leads and deals the user may see, by name, organization, number or email."""
	access.require_inbox_user()
	query = (query or "").strip()
	out = []
	for doctype, fields in (
		("CRM Deal", ["name", "organization", "lead_name", "status", "mobile_no"]),
		("CRM Lead", ["name", "lead_name", "organization", "status", "mobile_no"]),
	):
		like = f"%{query}%"
		or_filters = (
			[[f, "like", like] for f in ("name", "lead_name", "organization", "mobile_no", "email")]
			if query
			else None
		)
		filters = {"converted": 0} if doctype == "CRM Lead" else {}
		for r in frappe.get_list(
			doctype, fields=fields, filters=filters, or_filters=or_filters, order_by="modified desc", limit=8
		):
			out.append(
				{
					"doctype": doctype,
					"name": r.name,
					"title": r.get("organization") or r.get("lead_name") or r.name,
					"subtitle": " · ".join(
						p for p in (r.get("lead_name"), r.get("mobile_no"), r.status) if p
					),
				}
			)
	return out


def _relink(row, lead: str | None, deal: str | None, by_hand: bool):
	"""Point every chat row of this contact (or the group) at the record, and link its messages again,
	so the CRM WhatsApp tab and the timeline follow."""
	if row.is_group:
		group = frappe.db.get_value("Periskope Group", {"chat_id": row.chat_id})
		if group:
			frappe.db.set_value("Periskope Group", group, {"crm_lead": lead, "crm_deal": deal})
	chat.set_on_all(
		row.chat_id,
		{
			"crm_lead": lead,
			"crm_deal": deal,
			"linked_by_hand": 1 if by_hand else 0,
			"linked_by": frappe.session.user if by_hand else None,
		},
	)
	for name in frappe.get_all("Periskope Message", filters={"chat_id": row.chat_id}, pluck="name"):
		link_message(name)


@frappe.whitelist(methods=["POST"])
def link_record(chat_name: str, doctype: str, name: str) -> dict:
	"""Link the chat (and the contact's chats on our other numbers) to a lead or deal the user may
	see. A deal also keeps its lead."""
	access.require_inbox_user()
	row = _chat(chat_name)
	if doctype not in DOCTYPES or not frappe.has_permission(doctype, "read", name):
		frappe.throw(_("You cannot link this chat to that record."), frappe.PermissionError)
	lead, deal = (
		(frappe.db.get_value("CRM Deal", name, "lead"), name) if doctype == "CRM Deal" else (name, None)
	)
	_relink(row, lead, deal, by_hand=True)
	return get_context(chat_name)


@frappe.whitelist(methods=["POST"])
def unlink_record(chat_name: str) -> dict:
	"""Undo a link made by hand: the chat goes back to the automatic match by number."""
	access.require_inbox_user()
	row = _chat(chat_name)
	_relink(row, None, None, by_hand=False)
	return get_context(chat_name)


@frappe.whitelist(methods=["POST"])
def create_lead(
	chat_name: str, first_name: str, last_name: str = "", organization: str = "", email: str = ""
) -> dict:
	"""A new lead for this chat's customer, owned by the user, with WhatsApp as its source."""
	access.require_inbox_user()
	row = _chat(chat_name)
	if not (first_name or "").strip():
		frappe.throw(_("Enter the customer's name."))
	from periskope_connector.leads import ensure_lead_source

	lead = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": first_name.strip(),
			"last_name": (last_name or "").strip(),
			"organization": (organization or "").strip() or None,
			"email": (email or "").strip() or None,
			"mobile_no": f"+{row.contact_number}" if row.contact_number else None,
			"source": ensure_lead_source(),
			"lead_owner": frappe.session.user,
		}
	)
	lead.insert()  # as the user: the CRM's own rules apply
	_relink(row, lead.name, None, by_hand=True)
	return get_context(chat_name)


# --- Notes, tasks, status -----------------------------------------------------------------------


def _record_for_change(chat_name: str) -> tuple[str, str]:
	row = _chat(chat_name)
	record = _record(row)
	if not record:
		frappe.throw(_("Link this chat to a lead or deal first."))
	return record


@frappe.whitelist(methods=["POST"])
def add_note(chat_name: str, content: str, title: str = "") -> dict:
	access.require_inbox_user()
	doctype, name = _record_for_change(chat_name)
	if not (content or "").strip():
		frappe.throw(_("Write the note first."))
	frappe.get_doc(
		{
			"doctype": "FCRM Note",
			"title": (title or "").strip() or _("Note from WhatsApp"),
			"content": frappe.utils.escape_html(content.strip()).replace("\n", "<br>"),
			"reference_doctype": doctype,
			"reference_docname": name,
		}
	).insert()
	return get_context(chat_name)


@frappe.whitelist(methods=["POST"])
def add_task(
	chat_name: str,
	title: str,
	description: str = "",
	due_date: str | None = None,
	assigned_to: str | None = None,
	priority: str = "Medium",
) -> dict:
	access.require_inbox_user()
	doctype, name = _record_for_change(chat_name)
	if not (title or "").strip():
		frappe.throw(_("Give the task a title."))
	frappe.get_doc(
		{
			"doctype": "CRM Task",
			"title": title.strip(),
			"description": frappe.utils.escape_html(description or ""),
			"due_date": due_date or None,
			"assigned_to": assigned_to or frappe.session.user,
			"priority": priority if priority in ("Low", "Medium", "High") else "Medium",
			"status": "Todo",
			"reference_doctype": doctype,
			"reference_docname": name,
		}
	).insert()
	return get_context(chat_name)


@frappe.whitelist()
def lost_reasons() -> list[str]:
	"""The CRM's reasons for losing a lead or deal (asked when the status is a "Lost" one)."""
	access.require_inbox_user()
	if not frappe.db.exists("DocType", "CRM Lost Reason"):
		return []
	return frappe.get_all("CRM Lost Reason", pluck="name", order_by="name asc")


@frappe.whitelist(methods=["POST"])
def set_status(
	chat_name: str, status: str, lost_reason: str | None = None, lost_notes: str | None = None
) -> dict:
	access.require_inbox_user()
	doctype, name = _record_for_change(chat_name)
	if status not in [s["value"] for s in statuses(doctype)]:
		frappe.throw(_("Unknown status {0}").format(status))
	doc = frappe.get_doc(doctype, name)
	doc.status = status
	if lost_reason:
		doc.lost_reason = lost_reason
	if lost_notes:
		doc.lost_notes = lost_notes
	doc.save()  # as the user: the CRM asks for a lost reason itself
	return get_context(chat_name)


@frappe.whitelist(methods=["POST"])
def complete_task(chat_name: str, task: str | int) -> dict:  # CRM Task names are numbers
	access.require_inbox_user()
	doctype, name = _record_for_change(chat_name)
	doc = frappe.get_doc("CRM Task", task)
	if (doc.reference_doctype, doc.reference_docname) != (doctype, name):
		frappe.throw(_("That task is not on this chat's record."), frappe.PermissionError)
	doc.status = "Done"
	doc.save()
	return get_context(chat_name)


# --- From the CRM -------------------------------------------------------------------------------


@frappe.whitelist()
def chats_for_record(doctype: str, name: str) -> list[dict]:
	"""The chats of a lead or deal the user may open, on every number, newest first (the CRM's
	"Open WhatsApp inbox" button)."""
	access.require_inbox_user()
	if doctype not in DOCTYPES or not frappe.has_permission(doctype, "read", name):
		return []
	chat_ids = chat.for_record(doctype, name)
	rows = frappe.get_all(
		"Periskope Chat",
		filters={"chat_id": ["in", chat_ids or [""]]},
		fields=["name", "whatsapp_number", "last_message_at"],
		order_by="last_message_at desc",
	)
	return [
		{
			"name": r.name,
			"number_label": numbers.label(r.whatsapp_number),
			"last_message_at": r.last_message_at,
		}
		for r in rows
		if access.can_see_chat(r.name)
	]
