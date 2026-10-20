"""One Periskope Chat row per WhatsApp chat: which lead / deal / organization it belongs to.

Nobody pauses the AI: it answers a customer only when no rep has answered within the set time
(autoreply.py), so a rep who is talking to the customer is never interrupted.
"""

import frappe
from frappe.utils import get_datetime

from periskope_connector.system import insert_as_system


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


def for_record(doctype: str, name: str) -> list[str]:
	"""The chats linked to a CRM lead or deal (a deal also owns its lead's chats)."""
	field = "crm_deal" if doctype == "CRM Deal" else "crm_lead"
	chats = set(frappe.get_all("Periskope Chat", filters={field: name}, pluck="name"))
	if doctype == "CRM Deal":
		lead = frappe.db.get_value("CRM Deal", name, "lead")
		if lead:
			chats |= set(frappe.get_all("Periskope Chat", filters={"crm_lead": lead}, pluck="name"))
	return sorted(chats)
