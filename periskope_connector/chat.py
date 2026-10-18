"""One Periskope Chat row per WhatsApp chat: which lead / deal / organization it belongs to, and
whether the AI may reply in it.

A rep who replies takes the chat over: the AI is paused for it ("Rep replied") and resumes on its
own after `ai_resume_after_hours` (Periskope Settings > Advanced; 0 keeps it paused). A rep can also
pause or resume it from the lead or deal in the CRM ("Manual"), which only a rep resumes.
"""

from datetime import timedelta

import frappe
from frappe.utils import get_datetime, now_datetime

from periskope_connector import safety
from periskope_connector.system import insert_as_system

DEFAULT_RESUME_HOURS = 24


def ensure(message) -> str | None:
	"""Create or refresh the chat row of a saved Periskope Message (a dict-like with its fields).
	Never raises: a chat row must not stop a message from being saved."""
	try:
		values = {
			"is_group": message.is_group or 0,
			"contact_number": message.contact_number or "",
			"org_phone": message.org_phone or "",
			"last_message_at": message.message_timestamp,
			"crm_lead": message.crm_lead,
			"crm_deal": message.crm_deal,
		}
		if message.get("chat_name") or message.get("sender_name"):
			values["chat_name"] = message.get("chat_name") or (
				message.get("sender_name") if message.direction == "Incoming" else None
			)
		values = {k: v for k, v in values.items() if v is not None}
		last = frappe.db.get_value("Periskope Chat", message.chat_id, "last_message_at")
		if (
			last
			and message.message_timestamp
			and get_datetime(last) > get_datetime(message.message_timestamp)
		):
			values.pop("last_message_at", None)  # an older message linked again
		if frappe.db.exists("Periskope Chat", message.chat_id):
			if not message.crm_lead and not message.crm_deal:
				values.pop("crm_lead", None)
				values.pop("crm_deal", None)
			frappe.db.set_value("Periskope Chat", message.chat_id, values, update_modified=False)
		else:
			insert_as_system({"doctype": "Periskope Chat", "chat_id": message.chat_id, **values})
		return message.chat_id
	except Exception:
		frappe.log_error(title="Periskope: could not update the chat row")
		return None


def _resume_hours() -> float:
	value = frappe.db.get_single_value("Periskope Settings", "ai_resume_after_hours")
	return DEFAULT_RESUME_HOURS if value is None else float(value)


def is_paused(chat_id: str) -> bool:
	"""True while the AI must not reply in this chat. A takeover by a rep's reply ends by itself."""
	row = frappe.db.get_value(
		"Periskope Chat", chat_id, ["ai_paused", "pause_reason", "paused_on"], as_dict=True
	)
	if not row or not row.ai_paused:
		return False
	hours = _resume_hours()
	if row.pause_reason == "Rep replied" and hours and row.paused_on:
		if get_datetime(row.paused_on) < now_datetime() - timedelta(hours=hours):
			resume(chat_id, user="Administrator", log=False)
			return False
	return True


def pause(chat_id: str, user: str, reason: str) -> None:
	"""Pause (or keep paused, with a fresh time) the AI in this chat. Logged only when it changes."""
	if not frappe.db.exists("Periskope Chat", chat_id):
		insert_as_system(
			{"doctype": "Periskope Chat", "chat_id": chat_id, "is_group": safety.is_group(chat_id)}
		)
	was = frappe.db.get_value("Periskope Chat", chat_id, ["ai_paused", "pause_reason"], as_dict=True)
	if was.ai_paused and was.pause_reason == "Manual" and reason != "Manual":
		return  # a manual pause stays manual: only a rep resumes it
	frappe.db.set_value(
		"Periskope Chat",
		chat_id,
		{"ai_paused": 1, "pause_reason": reason, "paused_by": user, "paused_on": now_datetime()},
		update_modified=False,
	)
	if not was.ai_paused:
		safety.log("Pause", safety.LIVE, f"AI replies paused ({reason})", chat_id=chat_id, origin=reason)


def resume(chat_id: str, user: str, log: bool = True) -> None:
	frappe.db.set_value(
		"Periskope Chat",
		chat_id,
		{"ai_paused": 0, "pause_reason": "", "paused_by": user, "paused_on": None},
		update_modified=False,
	)
	if log:
		safety.log("Resume", safety.LIVE, "AI replies resumed", chat_id=chat_id, origin="Manual")


def for_record(doctype: str, name: str) -> list[str]:
	"""The chats linked to a CRM lead or deal (a deal also owns its lead's chats)."""
	field = "crm_deal" if doctype == "CRM Deal" else "crm_lead"
	chats = set(frappe.get_all("Periskope Chat", filters={field: name}, pluck="name"))
	if doctype == "CRM Deal":
		lead = frappe.db.get_value("CRM Deal", name, "lead")
		if lead:
			chats |= set(frappe.get_all("Periskope Chat", filters={"crm_lead": lead}, pluck="name"))
	return sorted(chats)
