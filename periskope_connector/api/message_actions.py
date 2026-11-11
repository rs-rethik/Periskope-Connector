"""Actions on one WhatsApp message from the inbox, through Periskope (POST /message/{id}/react, /edit,
/delete, /forward, /pin):

- react with an emoji (or take the reaction back), on any message;
- edit our own text message within WhatsApp's 15 minutes;
- delete our own message for everyone;
- forward a message to other chats on the same number;
- pin a message in the chat for 1, 7 or 30 days, or unpin it.

Whoever may open the chat may react, forward and pin; editing and deleting are for who sent the
message, or a manager. The customer sees every one of these, so test mode applies as for sending: only
test numbers' chats. Messages on the Meta number are not handled by Periskope, so not here.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from periskope_connector import access, meta, safety
from periskope_connector.client import request

EDIT_MINUTES = 15
PIN_DAYS = (1, 7, 30)
MAX_FORWARD = 20


def _message(name: str) -> frappe._dict:
	access.require_inbox_user()
	m = frappe.db.get_value(
		"Periskope Message",
		name,
		[
			"name",
			"message_id",
			"periskope_chat",
			"chat_id",
			"direction",
			"message_type",
			"body",
			"sent_by",
			"message_timestamp",
			"whatsapp_number",
			"org_phone",
			"is_deleted",
			"reactions",
		],
		as_dict=True,
	)
	if not m or not m.periskope_chat or not access.can_see_chat(m.periskope_chat):
		frappe.throw(_("You cannot open this message."), frappe.PermissionError)
	m.number = m.whatsapp_number or "".join(ch for ch in (m.org_phone or "").split("@")[0] if ch.isdigit())
	if meta.is_meta(m.number):
		frappe.throw(_("This is not possible on the Meta number."))
	if not m.message_id or m.message_id.startswith("local_"):
		frappe.throw(_("This message has not reached WhatsApp yet."))
	if m.is_deleted:
		frappe.throw(_("This message was deleted."))
	return m


def _allowed(chat_id: str):
	"""The customer sees it: only a test number's chat in test mode."""
	try:
		safety.check_send(chat_id)
	except safety.SendNotAllowed:
		frappe.throw(_("Test mode is on: this is only possible in the test numbers' chats."))


def _own(m):
	if m.direction != "Outgoing":
		frappe.throw(_("Only our own messages can be changed."))
	if m.sent_by != frappe.session.user and not access.is_manager():
		frappe.throw(_("Only who sent it, or a manager, can change this message."), frappe.PermissionError)


def _row(name: str) -> dict:
	from periskope_connector.api.compose import _row as message_row

	row = message_row(name)
	row.reactions = frappe.parse_json(row.reactions) if row.get("reactions") else {}
	return row


@frappe.whitelist(methods=["POST"])
def react(message: str, emoji: str = "") -> dict:
	"""React with an emoji; an empty emoji takes our reaction back."""
	access.require_inbox_user()
	m = _message(message)
	_allowed(m.chat_id)
	emoji = (emoji or "").strip()
	if len(emoji) > 16:
		frappe.throw(_("Pick one emoji."))
	request("POST", f"message/{m.message_id}/react", phone=m.number, json={"reaction": emoji or None})
	reactions = frappe.parse_json(m.reactions) if m.reactions else {}
	if emoji:
		reactions[m.number] = emoji
	else:
		reactions.pop(m.number, None)
	frappe.db.set_value(
		"Periskope Message",
		m.name,
		"reactions",
		frappe.as_json(reactions) if reactions else None,
		update_modified=False,
	)
	return _row(m.name)


@frappe.whitelist(methods=["POST"])
def edit(message: str, text: str) -> dict:
	"""Change the text (or a file's caption) of our own message, within WhatsApp's 15 minutes."""
	access.require_inbox_user()
	m = _message(message)
	_own(m)
	_allowed(m.chat_id)
	text = (text or "").strip()
	if not text:
		frappe.throw(_("Type the new text."))
	if m.message_timestamp and get_datetime(m.message_timestamp) < add_to_date(
		now_datetime(), minutes=-EDIT_MINUTES
	):
		frappe.throw(_("WhatsApp lets a message be edited only in its first 15 minutes."))
	request("POST", f"message/{m.message_id}/edit", phone=m.number, json={"edited_body": text})
	frappe.db.set_value(
		"Periskope Message",
		m.name,
		{"previous_body": m.body, "body": text, "edited_on": now_datetime()},
		update_modified=False,
	)
	return _row(m.name)


@frappe.whitelist(methods=["POST"])
def delete(message: str) -> dict:
	"""Delete our own message for everyone (WhatsApp allows it for a limited time after sending)."""
	access.require_inbox_user()
	m = _message(message)
	_own(m)
	_allowed(m.chat_id)
	request("POST", f"message/{m.message_id}/delete", phone=m.number)
	frappe.db.set_value(
		"Periskope Message", m.name, {"is_deleted": 1, "deleted_on": now_datetime()}, update_modified=False
	)
	return _row(m.name)


@frappe.whitelist(methods=["POST"])
def forward(message: str, chats: str | list) -> dict:
	"""Forward the message to other chats of the same number: {results: [{chat, ok}]}. The forwarded
	copies come back by webhook like any message we send."""
	access.require_inbox_user()
	m = _message(message)
	chats = frappe.parse_json(chats) if isinstance(chats, str) else chats
	chats = list(dict.fromkeys(chats or []))
	if not chats or len(chats) > MAX_FORWARD:
		frappe.throw(_("Pick 1 to {0} chats.").format(MAX_FORWARD))
	targets = []
	for name in chats:
		row = frappe.db.get_value(
			"Periskope Chat", name, ["name", "chat_id", "chat_name", "whatsapp_number"], as_dict=True
		)
		if not row or not access.can_see_chat(name):
			frappe.throw(_("You cannot write in one of the chats."), frappe.PermissionError)
		if row.whatsapp_number != m.number:
			frappe.throw(
				_("{0} is on another number: forward only to chats on the same number.").format(
					row.chat_name or row.chat_id
				)
			)
		_allowed(row.chat_id)
		targets.append(row)
	result = (
		request(
			"POST",
			f"message/{m.message_id}/forward",
			phone=m.number,
			json={"forward_chat_ids": [t.chat_id for t in targets]},
		)
		or {}
	)
	return {
		"results": [
			{"chat": t.name, "title": t.chat_name or t.chat_id, "ok": bool(result.get(t.chat_id, True))}
			for t in targets
		]
	}


@frappe.whitelist(methods=["POST"])
def pin(message: str, pinned: int | str = 1, days: int | str = 7) -> dict:
	"""Pin the message at the top of the WhatsApp chat for 1, 7 or 30 days, or unpin it."""
	access.require_inbox_user()
	m = _message(message)
	_allowed(m.chat_id)
	body = {"pin": bool(frappe.utils.cint(pinned))}
	if body["pin"]:
		days = frappe.utils.cint(days)
		if days not in PIN_DAYS:
			frappe.throw(_("Pin for 1, 7 or 30 days."))
		body["time"] = days
	request("POST", f"message/{m.message_id}/pin", phone=m.number, json=body)
	return {"pinned": body["pin"]}


def fresh_media_path(message_id: str, number: str) -> str | None:
	"""A new link to a message's file from Get Message, for when the saved link has expired."""
	try:
		data = request("GET", f"message/{message_id}", phone=number, missing_ok=True) or {}
	except Exception:
		return None
	data = data.get("message", data) if isinstance(data, dict) else {}
	return (data.get("media") or {}).get("path") or None
