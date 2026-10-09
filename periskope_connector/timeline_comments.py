"""Show a classified WhatsApp message in the CRM timeline of its lead or deal, as a comment.

Only messages whose tag has a Timeline Comment action get one (by default every tag but the
default one, and not skipped messages such as "ok" or "thanks"), so the timeline is not filled with chatter. A message gets at most one
comment. A deal's timeline includes its lead's comments, so the comment goes on the lead when
there is one and on the deal otherwise.

Comment modes (Periskope Settings): one comment per message (default), or "Burst", where messages
of the same chat inside the window grow one shared comment, or none at all.
"""

from datetime import timedelta

import frappe
from frappe.utils import escape_html

from periskope_connector import actions
from periskope_connector.system import insert_as_system

MODE_PER_MESSAGE = "One per message"
MODE_BURST = "Burst"
MODE_OFF = "Off"
# The message fields the comment is built from.
MESSAGE_FIELDS = [
	"name",
	"direction",
	"is_group",
	"chat_id",
	"chat_name",
	"contact_number",
	"sender_phone",
	"message_timestamp",
	"message_type",
	"body",
	"media_filename",
	"ai_summary",
	"ai_category",
	"severity",
	"rep",
	"crm_lead",
	"crm_deal",
	"timeline_comment",
]
MAX_COMMENT_CHARS = 12000  # start a new burst comment rather than grow an endless one


def _target(doc) -> tuple[str, str] | None:
	if doc.crm_lead:
		return "CRM Lead", doc.crm_lead
	if doc.crm_deal:
		return "CRM Deal", doc.crm_deal
	return None


def _header(doc) -> str:
	who = doc.chat_name or doc.contact_number or ""
	if doc.is_group and doc.sender_phone:
		who = f"{who} ({doc.sender_phone})"
	when = doc.message_timestamp.strftime("%d %b %Y %H:%M") if doc.message_timestamp else ""
	return f"<p><b>WhatsApp {escape_html(doc.direction.lower())}</b> &middot; {escape_html(who)} &middot; {when}</p>"


def _body_line(doc) -> str:
	body = doc.body or (f"[{doc.media_filename}]" if doc.media_filename else f"[{doc.message_type}]")
	return f"<p>{escape_html(body).replace(chr(10), '<br>')}</p>"


def _summary_line(doc) -> str:
	summary = f": {escape_html(doc.ai_summary)}" if doc.ai_summary else ""
	return f"<p><i>{escape_html((doc.ai_category or '').replace('_', ' '))}{summary}</i></p>"


def _content(doc) -> str:
	return _header(doc) + _body_line(doc) + _summary_line(doc)


def _line(doc) -> str:
	"""One burst entry: shorter header, same body and summary."""
	when = doc.message_timestamp.strftime("%H:%M") if doc.message_timestamp else ""
	return (
		f"<p><b>{escape_html(doc.direction.lower())}</b> &middot; {when}</p>"
		+ _body_line(doc)
		+ _summary_line(doc)
	)


def _settings() -> tuple[str, int]:
	mode = frappe.db.get_single_value("Periskope Settings", "comment_mode") or MODE_PER_MESSAGE
	minutes = frappe.db.get_single_value("Periskope Settings", "burst_minutes") or 30
	return mode, int(minutes)


def _burst_comment(doc, minutes: int) -> str | None:
	"""The comment of a message of the same chat, on the same lead/deal, inside the burst window:
	the one to append to, if there is one."""
	rows = frappe.get_all(
		"Periskope Message",
		filters=[
			["chat_id", "=", doc.chat_id],
			["name", "!=", doc.name],
			["timeline_comment", "is", "set"],
			["message_timestamp", ">", doc.message_timestamp - timedelta(minutes=minutes)],
			["message_timestamp", "<=", doc.message_timestamp],
		],
		fields=["name", "timeline_comment", "crm_lead", "crm_deal"],
		order_by="message_timestamp desc",
		limit=1,
	)
	if not rows:
		return None
	row = rows[0]
	# Same chat can still hit two records (lead inherited by a deal): only merge the same target.
	if (row.crm_lead, row.crm_deal) != (doc.crm_lead, doc.crm_deal):
		return None
	return row.timeline_comment


def add_comment(message: str) -> str | None:
	"""Add the timeline comment of a linked, tagged message. Returns its name, or None."""
	doc = frappe.db.get_value("Periskope Message", message, MESSAGE_FIELDS, as_dict=True)
	if not doc:
		return None
	target = _target(doc)
	if doc.timeline_comment or not target or not actions.matching(doc, actions.TIMELINE_COMMENT):
		return None

	mode, minutes = _settings()
	if mode == MODE_OFF:
		return None

	if mode == MODE_BURST:
		shared = _burst_comment(doc, minutes)
		if shared:
			content = frappe.db.get_value("Comment", shared, "content") or ""
			if len(content) + len(line := _line(doc)) <= MAX_COMMENT_CHARS:
				frappe.db.set_value("Comment", shared, "content", content + line, update_modified=False)
				frappe.db.set_value(
					"Periskope Message", message, "timeline_comment", shared, update_modified=False
				)
				return shared

	author = doc.rep if doc.rep and frappe.db.get_value("User", doc.rep, "enabled") else "Administrator"
	comment = insert_as_system(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": target[0],
			"reference_name": target[1],
			"comment_email": author,
			"owner": author,
			"content": _content(doc),
		}
	)
	frappe.db.set_value("Periskope Message", message, "timeline_comment", comment.name, update_modified=False)
	return comment.name
