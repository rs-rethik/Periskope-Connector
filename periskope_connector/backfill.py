"""Recover messages that a missed webhook never delivered, by reading the chat history from Periskope.

Webhooks are fire-and-forget: if the site was down or a delivery failed, that message is simply
absent. This job lists each linked number's recently active chats, reads their latest messages and
saves the ones we do not have. Messages go through the normal `create_message` path, so they are
classified, linked and keyword-checked like any other, and the SLA check (reminders.run) then treats
an unanswered recovered message exactly like a live one.
"""

from datetime import datetime, timedelta, timezone

import frappe
import pytz
from dateutil import parser as date_parser
from frappe.utils.data import get_system_timezone

from periskope_connector.client import request
from periskope_connector.linking import refresh_group_name
from periskope_connector.messages import create_message

LOOKBACK_DAYS = 1  # the scheduled run looks back at least this far
MAX_LOOKBACK_DAYS = 7  # and, after an outage, back to the number's newest saved message, at most this
OVERLAP_HOURS = 1  # read a little before the newest saved message (webhooks arrive out of order)
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
	from periskope_connector import numbers

	return numbers.ready()


def _active_chats(phone: str, cutoff: datetime, max_chats: int = MAX_CHATS) -> list[str]:
	"""Ids of the chats that had a message since `cutoff`."""
	chat_ids = []
	for offset in range(0, max_chats, CHAT_PAGE):
		data = request("GET", f"chats?limit={CHAT_PAGE}&offset={offset}", phone=phone)
		chats = data.get("chats", []) if isinstance(data, dict) else []
		for chat in chats:
			if (chat.get("chat_id") or "").endswith("@g.us") and chat.get("chat_name"):
				# a group renamed in WhatsApp: its new name (and what that name links to) is kept
				refresh_group_name(chat["chat_id"], chat["chat_name"])
			last = _as_utc((chat.get("latest_message") or {}).get("timestamp") or chat.get("updated_at"))
			if chat.get("chat_id") and last and last >= cutoff:
				chat_ids.append(chat["chat_id"])
		if len(chats) < CHAT_PAGE:
			break
	return chat_ids


def _missing_messages(
	chat_id: str, phone: str, cutoff: datetime, max_pages: int = MAX_MESSAGE_PAGES
) -> list[dict]:
	"""The chat's messages since `cutoff` that are not saved yet, oldest first."""
	missing = []
	for page in range(max_pages):
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


def _cutoff(phone: str, now: datetime) -> datetime:
	"""Where the scheduled run starts for one number: a day back, or further when the newest message
	saved for it is older (the site or the webhook was down), up to MAX_LOOKBACK_DAYS."""
	newest = frappe.db.get_value(
		"Periskope Message", {"org_phone": phone}, "max(message_timestamp)"
	)  # site time, naive
	day = now - timedelta(days=LOOKBACK_DAYS)
	if not newest:
		return day
	local = pytz.timezone(get_system_timezone()).localize(frappe.utils.get_datetime(newest))
	since = local.astimezone(timezone.utc) - timedelta(hours=OVERLAP_HOURS)
	return max(min(since, day), now - timedelta(days=MAX_LOOKBACK_DAYS))


def run(days: int | float | None = None) -> dict:
	"""Save the messages the webhooks missed: by default since each number's newest saved message (at
	least a day, at most MAX_LOOKBACK_DAYS), or of the last `days` days.

	Scheduler (hourly), or by hand:
	bench --site <site> execute periskope_connector.backfill.run --kwargs '{"days": 7}'
	"""
	if not frappe.db.get_single_value("Periskope Settings", "enabled"):
		return {"recovered": 0, "chats": 0}

	now = datetime.now(timezone.utc)
	recovered = chats_checked = failed = 0
	for phone in _linked_phones():
		cutoff = now - timedelta(days=float(days)) if days else _cutoff(phone, now)
		try:
			chat_ids = _active_chats(phone, cutoff)
		except Exception:
			frappe.log_error(title=f"Periskope: back-fill could not list chats for {phone}")
			continue
		for chat_id in chat_ids:
			chats_checked += 1
			try:
				missing = _missing_messages(chat_id, phone, cutoff)
			except Exception:
				frappe.log_error(title=f"Periskope: back-fill could not read chat {chat_id}")
				continue
			for data in missing:
				# Chronological, so a rep's reply marks the customer messages before it as replied.
				# One message that cannot be saved must not undo or block the rest of the chat.
				frappe.db.savepoint("periskope_backfill_message")
				try:
					if create_message({"event": "message.created", "data": data}):
						recovered += 1
				except Exception:
					frappe.db.rollback(save_point="periskope_backfill_message")
					failed += 1
					frappe.log_error(
						title=f"Periskope: back-fill could not save message {data.get('message_id')}"
					)
			frappe.db.commit()  # a job: each chat's recovered messages are kept
	return {"recovered": recovered, "chats": chats_checked, **({"failed": failed} if failed else {})}
