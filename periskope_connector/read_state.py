"""Who reads a chat: the person it is assigned to, or a member of its team.

A chat counts as read for everyone in the inbox once its assigned sales person, or a member of its
assigned team, opens it. An unassigned chat with no team counts as read when any sales user opens it.
A manager or admin opening a chat that is not theirs changes nothing: it stays unread for the rep,
and the customer does not see blue ticks.

When a reader opens a chat with new customer messages, the customer is shown them as read: Periskope's
mark-read (POST /chats/{chat_id}/read) for a Periskope number, a read receipt for a Meta number. Test
mode applies, like a message: only allowed chats get it."""

import json

import frappe
from frappe.utils import get_datetime, now_datetime

from periskope_connector import access

FIELDS = [
	"name",
	"chat_id",
	"whatsapp_number",
	"_assign",
	"team",
	"last_incoming_at",
	"read_at",
	"customer_read_at",
]


def _chat(chat: str) -> frappe._dict | None:
	return frappe.db.get_value("Periskope Chat", chat, FIELDS, as_dict=True)


def is_reader(row, user: str) -> bool:
	"""Whether this user opening the chat makes it read."""
	assigned = json.loads(row.get("_assign") or "[]")
	if user in assigned:
		return True
	if row.get("team") and frappe.db.exists(
		"Periskope Team Member", {"parent": row.team, "parenttype": "Periskope Team", "user": user}
	):
		return True
	if not assigned and not row.get("team"):
		return not access.is_manager(user)  # nobody owns it yet: a sales user reading it counts
	return False


def opened(chat: str, user: str | None = None) -> bool:
	"""A user opened the chat. Returns True when that made it read."""
	user = user or frappe.session.user
	row = _chat(chat)
	if not row or not is_reader(row, user):
		return False
	_set_read(row, user)
	return True


def mark_read(chat: str, user: str | None = None):
	"""The "Mark as read" action: read in the inbox for everyone, whoever does it. The customer's
	blue ticks still come only from the chat's own reader."""
	user = user or frappe.session.user
	row = _chat(chat)
	if not row:
		return
	if is_reader(row, user):
		_set_read(row, user)
	else:
		frappe.db.set_value(
			"Periskope Chat", chat, {"read_at": now_datetime(), "read_by": user}, update_modified=False
		)


def _set_read(row, user: str):
	# What the customer has not been shown as read yet: judged by the last receipt that went out
	# (customer_read_at), not by read_at, which a manager's "Mark as read" also sets and which is
	# set even when the receipt could not be sent. The chat's reader opening it later still sends it.
	from periskope_connector import safety

	new_messages = (
		row.last_incoming_at
		and (
			not row.customer_read_at
			or get_datetime(row.customer_read_at) < get_datetime(row.last_incoming_at)
		)
		and safety.send_mode(row.chat_id) == safety.LIVE  # test mode: no receipt, so no job either
	)
	frappe.db.set_value(
		"Periskope Chat", row.name, {"read_at": now_datetime(), "read_by": user}, update_modified=False
	)
	if new_messages:
		frappe.enqueue(
			"periskope_connector.read_state.send_receipt",
			chat=row.name,
			queue="short",
			enqueue_after_commit=True,
		)


def send_receipt(chat: str):
	"""Show the customer the chat's messages as read. Never raises."""
	from periskope_connector import client, meta, safety

	row = _chat(chat)
	if not row or not row.whatsapp_number:
		return
	try:
		if safety.send_mode(row.chat_id) != safety.LIVE:
			return  # test mode: like a message, only allowed chats see blue ticks
		if meta.is_meta(row.whatsapp_number):
			if not _meta_receipt(row):
				return
		else:
			client.request(
				"POST", f"chats/{row.chat_id}/read", phone=row.whatsapp_number, text=True
			)  # no body
		frappe.db.set_value("Periskope Chat", chat, "customer_read_at", now_datetime(), update_modified=False)
	except Exception:
		frappe.log_error(title=f"Periskope: could not mark chat {chat} read for the customer")


def _meta_receipt(row) -> bool:
	"""A read receipt for the customer's latest message; WhatsApp then shows the earlier ones read too."""
	from periskope_connector import meta

	message_id = frappe.db.get_value(
		"Periskope Message",
		{"periskope_chat": row.name, "direction": "Incoming", "message_id": ["like", f"{meta.PREFIX}%"]},
		"message_id",
		order_by="message_timestamp desc",
	)
	if not message_id:
		return False
	name = frappe.db.get_value("WhatsApp Message", {"message_id": message_id[len(meta.PREFIX) :]})
	if not name:
		return False
	frappe.get_doc("WhatsApp Message", name).send_read_receipt()
	return True
