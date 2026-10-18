"""What the CRM pages call (lead and deal form scripts, see install.FORM_SCRIPT). Sales users use
these, so each one checks access to the lead or deal itself."""

import frappe
from frappe import _

from periskope_connector import chat
from periskope_connector.api.permission import require_record_access


@frappe.whitelist()
def get_ai_state(doctype: str, name: str) -> dict:
	"""Whether the AI may reply in the WhatsApp chats of this lead or deal."""
	require_record_access(doctype, name, "read")
	chats = chat.for_record(doctype, name)
	paused = [c for c in chats if chat.is_paused(c)]
	return {"chats": len(chats), "paused": bool(chats) and len(paused) == len(chats)}


@frappe.whitelist()
def set_ai_paused(doctype: str, name: str, paused: int | str | bool) -> dict:
	"""Pause or resume the AI replies in every WhatsApp chat of this lead or deal."""
	require_record_access(doctype, name, "write")
	chats = chat.for_record(doctype, name)
	if not chats:
		frappe.throw(_("This record has no WhatsApp chat yet."))
	pause = str(paused).lower() in ("1", "true")
	for chat_id in chats:
		if pause:
			chat.pause(chat_id, frappe.session.user, "Manual")
		else:
			chat.resume(chat_id, frappe.session.user)
	return get_ai_state(doctype, name)
