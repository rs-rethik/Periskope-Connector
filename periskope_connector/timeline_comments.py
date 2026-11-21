"""Show a WhatsApp message in the CRM timeline of its lead or deal, as a comment.

Only messages the AI marked as mattering for the record get one (on_timeline: a requirement, an
order, a price, a complaint, a document), so the timeline is not filled with chatter. A message gets
at most one comment, with the AI's reading of its attachment when it has one. A deal's timeline
includes its lead's comments, so the comment goes on the lead when there is one and on the deal
otherwise.
"""

import frappe
from frappe.utils import escape_html

from periskope_connector import safety
from periskope_connector.system import insert_as_system

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
	"on_timeline",
	"attachment_summary",
]


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


def _attachment_line(doc) -> str:
	if not doc.attachment_summary:
		return ""
	return f"<p>{escape_html(doc.attachment_summary)}</p>"


def _content(doc) -> str:
	return _header(doc) + _body_line(doc) + _attachment_line(doc) + _summary_line(doc)


def _app_user() -> str:
	from periskope_connector.ai_user import AI_USER

	return AI_USER if frappe.db.exists("User", AI_USER) else "Administrator"


def add_comment(message: str) -> str | None:
	"""Add the timeline comment of a linked, tagged message. Returns its name, or None. A message
	that has its comment already keeps it; when its lead or deal changed (linked again by hand, a
	group mapped to another customer) the comment moves to the new record. Test mode: a dry run
	for a chat that is not on the allow list (safety.py)."""
	doc = frappe.db.get_value("Periskope Message", message, MESSAGE_FIELDS, as_dict=True)
	if not doc:
		return None
	target = _target(doc)
	if not target:
		return None
	if doc.timeline_comment:
		return _move_comment(doc, target)
	if not doc.on_timeline:
		return None

	def write():
		# the chat's rep, else the app's own user (ai_user.py) rather than Administrator
		author = doc.rep if doc.rep and frappe.db.get_value("User", doc.rep, "enabled") else _app_user()
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
		frappe.db.set_value(
			"Periskope Message", message, "timeline_comment", comment.name, update_modified=False
		)
		return comment.name

	return safety.crm_write(
		doc.chat_id,
		"Timeline",
		f"Timeline: WhatsApp message {message}",
		write,
		once=f"timeline:{message}",
		target_doctype="Comment",
		reference_doctype=target[0],
		reference_name=target[1],
	)


def _move_comment(doc, target: tuple[str, str]) -> str | None:
	"""Point the message's existing comment at the record the message is linked to now."""
	at = frappe.db.get_value(
		"Comment", doc.timeline_comment, ["reference_doctype", "reference_name"], as_dict=True
	)
	if not at or (at.reference_doctype, at.reference_name) == target:
		return None

	def write():
		# The comment is the app's own (insert_as_system above); only where it shows changes.
		frappe.db.set_value(
			"Comment",
			doc.timeline_comment,
			{"reference_doctype": target[0], "reference_name": target[1]},
			update_modified=False,
		)
		return doc.timeline_comment

	return safety.crm_write(
		doc.chat_id,
		"Timeline",
		f"Timeline: moved WhatsApp message {doc.name} to {target[1]}",
		write,
		once=f"timeline-move:{doc.name}:{target[1]}",
		target_doctype="Comment",
		reference_doctype=target[0],
		reference_name=target[1],
	)
