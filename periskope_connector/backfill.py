"""Recover messages that a missed webhook never delivered, by reading the chat history from Periskope.

Webhooks are fire-and-forget: if the site was down or a delivery failed, that message is simply
absent. This job lists each linked number's recently active chats, reads their latest messages and
saves the ones we do not have. Messages go through the normal `create_message` path, so they are
classified, linked and keyword-checked like any other, and the SLA check (reminders.run) then treats
an unanswered recovered message exactly like a live one.
"""

from datetime import datetime, timedelta, timezone

import frappe
from dateutil import parser as date_parser

from periskope_connector.client import request
from periskope_connector.messages import create_message

LOOKBACK_DAYS = 1  # the scheduled run; a manual run can look further back
CHAT_PAGE = 100
MAX_CHATS = 300  # per phone per run
MESSAGE_PAGE = 50
MAX_MESSAGE_PAGES = 4  # per chat per run: 200 messages


def _as_utc(value: str | None) -> datetime | None:
	if not value:
		return None
	dt = date_parser.parse(value)
	return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _linked_phones() -> list[str]:
	return frappe.get_all(
		"Periskope Phone",
		filters={"parent": "Periskope Settings", "is_ready": 1},
		pluck="number",
	)


def _active_chats(phone: str, cutoff: datetime) -> list[str]:
	"""Ids of the chats that had a message since `cutoff`."""
	chat_ids = []
	for offset in range(0, MAX_CHATS, CHAT_PAGE):
		data = request("GET", f"chats?limit={CHAT_PAGE}&offset={offset}", phone=phone)
		chats = data.get("chats", []) if isinstance(data, dict) else []
		for chat in chats:
			last = _as_utc((chat.get("latest_message") or {}).get("timestamp") or chat.get("updated_at"))
			if chat.get("chat_id") and last and last >= cutoff:
				chat_ids.append(chat["chat_id"])
		if len(chats) < CHAT_PAGE:
			break
	return chat_ids


def _missing_messages(chat_id: str, phone: str, cutoff: datetime) -> list[dict]:
	"""The chat's messages since `cutoff` that are not saved yet, oldest first."""
	missing = []
	for page in range(MAX_MESSAGE_PAGES):
		data = request(
			"GET",
			f"chats/{chat_id}/messages?limit={MESSAGE_PAGE}&offset={page * MESSAGE_PAGE}",
			phone=phone,
		)
		messages = data.get("messages", []) if isinstance(data, dict) else []  # newest first
		in_window = [m for m in messages if (_as_utc(m.get("timestamp")) or cutoff) >= cutoff]
		ids = [m["message_id"] for m in in_window if m.get("message_id")]
		known = set(
			frappe.get_all("Periskope Message", filters={"message_id": ["in", ids]}, pluck="message_id")
			if ids
			else []
		)
		missing += [m for m in in_window if m.get("message_id") and m["message_id"] not in known]
		if len(messages) < MESSAGE_PAGE or len(in_window) < len(messages):
			break  # reached the start of the chat or the lookback window
	return sorted(missing, key=lambda m: m.get("timestamp") or "")


def run(days: int | float = LOOKBACK_DAYS) -> dict:
	"""Save the messages of the last `days` days that the webhooks missed.

	Scheduler (hourly), or by hand:
	bench --site <site> execute periskope_connector.backfill.run --kwargs '{"days": 7}'
	"""
	if not frappe.db.get_single_value("Periskope Settings", "enabled"):
		return {"recovered": 0, "chats": 0}

	cutoff = datetime.now(timezone.utc) - timedelta(days=float(days))
	recovered = chats_checked = 0
	for phone in _linked_phones():
		try:
			chat_ids = _active_chats(phone, cutoff)
		except Exception:
			frappe.log_error(title=f"Periskope: back-fill could not list chats for {phone}")
			continue
		for chat_id in chat_ids:
			chats_checked += 1
			try:
				for data in _missing_messages(chat_id, phone, cutoff):
					# Chronological, so a rep's reply marks the customer messages before it as replied.
					if create_message({"event": "message.created", "data": data}):
						recovered += 1
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Periskope: back-fill failed for chat {chat_id}")
	return {"recovered": recovered, "chats": chats_checked}
