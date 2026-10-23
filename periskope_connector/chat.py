"""One Periskope Chat row per (our WhatsApp number, the contact's chat): its lead / deal /
organization, its inbox status and what the inbox list shows.

Periskope's chat id is the contact's WhatsApp id ("9163...@c.us", or a group's "...@g.us"), which is
the same on each of our numbers. A contact who writes to two of our numbers has two chats, each
answered from its own number; the lead or deal shows them together (for_record).

Nobody pauses the AI: it answers a customer only when no rep has answered within the set time
(autoreply.py), so a rep who is talking to the customer is never interrupted.
"""

import frappe
from frappe.utils import get_datetime

from periskope_connector.periskope_connector.doctype.periskope_chat.periskope_chat import key
from periskope_connector.text_rules import MEDIA_LABELS, readable_text

PREVIEW_CHARS = 140


def name_of(number: str | None, chat_id: str) -> str | None:
	"""The chat row of one contact on one of our numbers."""
	return frappe.db.get_value("Periskope Chat", {"chat_key": key(number, chat_id)})


def latest(chat_id: str) -> str | None:
	"""The contact's most recently active chat row, on any of our numbers."""
	return frappe.db.get_value(
		"Periskope Chat", {"chat_id": chat_id}, "name", order_by="last_message_at desc"
	)


def number_of(chat_id: str) -> str | None:
	"""The number this contact last wrote to (or we last wrote from): where a reply goes."""
	return frappe.db.get_value(
		"Periskope Chat", {"chat_id": chat_id}, "org_phone", order_by="last_message_at desc"
	)


def set_on_all(chat_id: str, values: dict):
	"""Facts about the contact (summary note, organization) belong on every number's chat."""
	for name in frappe.get_all("Periskope Chat", filters={"chat_id": chat_id}, pluck="name"):
		frappe.db.set_value("Periskope Chat", name, values, update_modified=False)


def preview(message) -> str:
	"""The one line the inbox list shows for a message."""
	if message.get("is_deleted"):
		return "This message was deleted"
	text = readable_text(message)
	if not text and message.get("message_type") in MEDIA_LABELS:
		text = MEDIA_LABELS[message.message_type] + (
			f" {message.media_filename}" if message.get("media_filename") else ""
		)
	return " ".join((text or "").split())[:PREVIEW_CHARS]


def ensure(message) -> str | None:
	"""Create or refresh the chat row of a saved Periskope Message (a dict-like with its fields) and
	point the message at it. Returns the chat row's name. Never raises: a chat row must not stop a
	message from being saved."""
	try:
		number = message.get("whatsapp_number") or message.org_phone or ""
		values = {
			"is_group": message.is_group or 0,
			"contact_number": message.contact_number or "",
			"org_phone": message.org_phone or "",
			"whatsapp_number": message.get("whatsapp_number"),
			"last_message_at": message.message_timestamp,
			"crm_lead": message.crm_lead,
			"crm_deal": message.crm_deal,
		}
		if message.get("chat_name") or message.get("sender_name"):
			values["chat_name"] = message.get("chat_name") or (
				message.get("sender_name") if message.direction == "Incoming" else None
			)
		values = {k: v for k, v in values.items() if v is not None}
		name = name_of(number, message.chat_id)
		last = name and frappe.db.get_value("Periskope Chat", name, "last_message_at")
		newest = not (
			last
			and message.message_timestamp
			and get_datetime(last) > get_datetime(message.message_timestamp)
		)
		if newest:
			values["last_message_preview"] = preview(message)
			values["last_message_direction"] = message.direction
		else:
			values.pop("last_message_at", None)  # an older message linked again
		if message.direction == "Incoming" and newest:
			values["last_incoming_at"] = message.message_timestamp
		if name:
			if frappe.db.get_value("Periskope Chat", name, "linked_by_hand"):
				values.pop("crm_lead", None)  # chosen by hand in the inbox: kept as it is
				values.pop("crm_deal", None)
			if not message.crm_lead and not message.crm_deal:
				values.pop("crm_lead", None)
				values.pop("crm_deal", None)
			frappe.db.set_value("Periskope Chat", name, values, update_modified=False)
		else:
			name = _insert_chat({"chat_id": message.chat_id, "status": "Open", **values})
			_lifecycle("new_chat", name, message.message_timestamp)
		if message.get("name") and message.get("periskope_chat") != name:
			frappe.db.set_value(
				"Periskope Message", message.name, "periskope_chat", name, update_modified=False
			)
		frappe.db.set_value(
			"Periskope Chat",
			name,
			"message_count",
			frappe.db.count("Periskope Message", {"periskope_chat": name}),
			update_modified=False,
		)
		if newest:
			_lifecycle("on_message", name, message)
		from periskope_connector import inbox_realtime

		inbox_realtime.chat_changed(name)
		return name
	except Exception:
		frappe.log_error(title="Periskope: could not update the chat row")
		return None


PREVIEW_FIELDS = ["name", "direction", "body", "message_type", "transcript", "is_deleted", "media_filename"]


def refresh(name: str):
	"""Work out a chat row's list fields again from its messages (after a migration or repair)."""
	last = frappe.get_all(
		"Periskope Message",
		filters={"periskope_chat": name},
		fields=[*PREVIEW_FIELDS, "message_timestamp"],
		order_by="message_timestamp desc",
		limit=1,
	)
	incoming = frappe.get_all(
		"Periskope Message",
		filters={"periskope_chat": name, "direction": "Incoming"},
		pluck="message_timestamp",
		order_by="message_timestamp desc",
		limit=1,
	)
	values = {
		"message_count": frappe.db.count("Periskope Message", {"periskope_chat": name}),
		"last_incoming_at": incoming[0] if incoming else None,
	}
	if last:
		values.update(
			last_message_at=last[0].message_timestamp,
			last_message_preview=preview(last[0]),
			last_message_direction=last[0].direction,
		)
	frappe.db.set_value("Periskope Chat", name, values, update_modified=False)


def _insert_chat(values: dict) -> str:
	"""Save a new chat row without the document hooks: Frappe would run the assignment rules on it
	there, and a broken rule must not stop a message from getting its chat. workflow.new_chat runs
	them afterwards, guarded."""
	doc = frappe.get_doc({"doctype": "Periskope Chat", **values})
	doc.chat_key = key(doc.whatsapp_number or doc.org_phone, doc.chat_id)
	doc.set_new_name()
	doc.set_user_and_timestamp()
	doc.db_insert()
	return doc.name


def _lifecycle(step: str, *args):
	"""Status, assignment and SLA clocks (workflow.py). Guarded: a message is saved whatever happens."""
	from periskope_connector import workflow

	try:
		frappe.db.savepoint("periskope_chat_lifecycle")
		getattr(workflow, step)(*args)
	except Exception:
		frappe.db.rollback(save_point="periskope_chat_lifecycle")
		frappe.log_error(title=f"Periskope: chat {step} failed")


def for_record(doctype: str, name: str) -> list[str]:
	"""The chat ids linked to a CRM lead or deal, on any of our numbers (a deal also owns its lead's
	chats)."""
	field = "crm_deal" if doctype == "CRM Deal" else "crm_lead"
	chats = set(frappe.get_all("Periskope Chat", filters={field: name}, pluck="chat_id"))
	if doctype == "CRM Deal":
		lead = frappe.db.get_value("CRM Deal", name, "lead")
		if lead:
			chats |= set(frappe.get_all("Periskope Chat", filters={"crm_lead": lead}, pluck="chat_id"))
	return sorted(chats)
