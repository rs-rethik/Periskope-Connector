"""Broadcasts: one message to many chats of a Periskope number, sent by Periskope one after another
(POST /message/broadcast), stopped (DELETE /message/broadcast/{id}), and followed message by message
(POST /message/queues with the broadcast's id). Managers only.

- The chats come from the inbox's filters (label, team, in the CRM or not, AI tag, search), on one
  number; a {{name}} in the message becomes each chat's name.
- Periskope charges one broadcast credit per chat; it refuses the whole broadcast when the credits are
  short. A gap between messages (default 3 seconds) keeps the number from looking like spam.
- Test mode: only the test numbers' chats (no groups), as for any message.
- Each sent message comes back by webhook like any other, and is marked as sent by whoever made the
  broadcast once its progress is read.
"""

import base64
import re

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from periskope_connector import access, attachments, meta, safety
from periskope_connector.api.inbox import _where
from periskope_connector.api.permission import require_manager
from periskope_connector.calls import _utc
from periskope_connector.client import request

DOCTYPE = "Periskope Broadcast"
MAX_CHATS = 1000
MAX_TEXT = 4096
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z_]+)\s*\}\}")
SENT = ("sent", "pending")
DELIVERED = ("delivered",)
READ = ("read", "played")
WAITING = ("queued", "scheduled", "sending")


def _manager():
	access.require_inbox_user()
	require_manager()
	if not access.is_manager():
		frappe.throw(_("Broadcasts are for managers."), frappe.PermissionError)


def _number(number: str) -> str:
	number = "".join(ch for ch in (number or "") if ch.isdigit())
	if not frappe.db.get_value("WhatsApp Number", number, "is_active") or meta.is_meta(number):
		frappe.throw(_("Choose an active Periskope number."))
	return number


@frappe.whitelist()
def recipients(number: str, filters: str | dict | None = None) -> dict:
	"""The chats on this number that match the inbox filters: {chats, total, allowed} where allowed
	says how many test mode lets through."""
	access.require_inbox_user()
	_manager()
	number = _number(number)
	filters = frappe.parse_json(filters) if isinstance(filters, str) else (filters or {})
	filters = {
		k: v
		for k, v in filters.items()
		if k in ("label", "team", "linked", "groups", "tag", "search", "status")
	}
	filters["number"] = number
	conditions, values = _where(filters)
	rows = frappe.db.sql(
		f"""select c.name, c.chat_id, c.chat_name, c.contact_number, c.is_group from `tabPeriskope Chat` c
		where {" and ".join(conditions)} order by c.last_message_at desc limit {MAX_CHATS + 1}""",
		values,
		as_dict=True,
	)
	for r in rows:
		r.title = r.chat_name or (f"+{r.contact_number}" if r.contact_number else r.chat_id)
		r.allowed = safety.send_mode(r.chat_id) == safety.LIVE
	return {"chats": rows[:MAX_CHATS], "more": len(rows) > MAX_CHATS, "test_mode": safety.test_mode()}


def _first_name(row) -> str:
	name = (row.chat_name or "").strip()
	return name.split(" ")[0] if name and not name.startswith("+") and not row.is_group else ""


@frappe.whitelist(methods=["POST"])
def send(
	number: str,
	chats: str | list,
	message: str = "",
	file: str | None = None,
	title: str = "",
	scheduled_at: str | None = None,
	delay: int | str = 3,
) -> dict:
	"""Broadcast the message to the chats (managers). Returns the broadcast."""
	access.require_inbox_user()
	_manager()
	number = _number(number)
	names = frappe.parse_json(chats) if isinstance(chats, str) else chats
	names = list(dict.fromkeys(names or []))
	if not names or len(names) > MAX_CHATS:
		frappe.throw(_("Pick 1 to {0} chats.").format(MAX_CHATS))
	message = (message or "").strip()
	if not message and not file:
		frappe.throw(_("Type a message or attach a file."))
	if len(message) > MAX_TEXT:
		frappe.throw(_("The message is longer than {0} characters.").format(MAX_TEXT))
	unknown = {p for p in PLACEHOLDER.findall(message) if p != "name"}
	if unknown:
		frappe.throw(
			_("Only {{{{name}}}} can be filled in for each chat ({0} cannot).").format(
				", ".join(sorted(unknown))
			)
		)
	rows = frappe.get_all(
		"Periskope Chat",
		filters={"name": ["in", names]},
		fields=["name", "chat_id", "chat_name", "whatsapp_number", "is_group"],
	)
	if len(rows) != len(names):
		frappe.throw(_("Some chats were not found."))
	for row in rows:
		if row.whatsapp_number != number or not access.can_see_chat(row.name):
			frappe.throw(_("{0} is not a chat of this number.").format(row.chat_name or row.chat_id))
		if safety.send_mode(row.chat_id) != safety.LIVE:
			frappe.throw(_("Test mode is on: {0} is not a test number.").format(row.chat_name or row.chat_id))
	delay = max(0, min(60, frappe.utils.cint(delay)))
	body = {"chat_ids": [r.chat_id for r in rows], "org_phones": [number], "delay": delay}
	if message:
		body["message"] = message
	if PLACEHOLDER.search(message):
		body["variables"] = [
			{"chat_id": r.chat_id, "values": {"name": _first_name(r) or "there"}} for r in rows
		]
	if file:
		body["media"] = _media(file)
	start = None
	if scheduled_at:
		start = get_datetime(scheduled_at)
		if start <= add_to_date(now_datetime(), minutes=1):
			frappe.throw(_("Choose a start time at least a minute from now, or send it now."))
		body["scheduled_at"] = _utc(start).replace(":00Z", ":00.000Z")
	made = request("POST", "message/broadcast", phone=number, json=body) or {}
	if not made.get("broadcast_id"):
		frappe.throw(_("Periskope did not start the broadcast."))
	doc = frappe.get_doc(
		{
			"doctype": DOCTYPE,
			"broadcast_id": made["broadcast_id"],
			"title": (title or message or _("Broadcast"))[:140],
			"whatsapp_number": number,
			"message": message,
			"file": frappe.db.get_value("File", file, "file_url") if file else None,
			"status": "Scheduled" if start else "Sending",
			"scheduled_at": start,
			"delay": delay,
			"recipient_count": len(rows),
			"queued": len(rows),
			"recipients": frappe.as_json(
				[{"chat": r.name, "chat_id": r.chat_id, "title": r.chat_name} for r in rows]
			),
		}
	).insert(ignore_permissions=True)
	return _out(doc)


def _media(file: str) -> dict:
	from periskope_connector.api.compose import _own_file, media_type

	f = _own_file(file)
	import mimetypes

	mimetype = mimetypes.guess_type(f.file_name or "")[0] or "application/octet-stream"
	return {
		"type": media_type(mimetype),
		"filedata": base64.b64encode(attachments._read(f.name)).decode(),
		"mimetype": mimetype,
		"filename": f.file_name,
	}


def _out(doc) -> dict:
	fields = (
		"name",
		"title",
		"whatsapp_number",
		"message",
		"file",
		"status",
		"scheduled_at",
		"delay",
		"recipient_count",
		"queued",
		"sent",
		"delivered",
		"read",
		"failed",
		"checked_on",
		"owner",
		"creation",
	)
	out = {f: doc.get(f) for f in fields}
	out["owner_name"] = frappe.utils.get_fullname(doc.owner)
	return out


@frappe.whitelist()
def broadcasts() -> list[dict]:
	"""The broadcasts, newest first."""
	access.require_inbox_user()
	_manager()
	return [
		_out(frappe.get_doc(DOCTYPE, n))
		for n in frappe.get_all(DOCTYPE, pluck="name", order_by="creation desc", limit=100)
	]


@frappe.whitelist(methods=["POST"])
def progress(broadcast: str) -> dict:
	"""Read each message's state from Periskope's queue: {broadcast, failed: [chat titles]}."""
	access.require_inbox_user()
	_manager()
	doc = frappe.get_doc(DOCTYPE, broadcast)
	jobs = request("POST", "message/queues", phone=doc.whatsapp_number, json={"broadcast_id": doc.name}) or []
	jobs = jobs if isinstance(jobs, list) else jobs.get("jobs") or []
	counts = {"queued": 0, "sent": 0, "delivered": 0, "read": 0, "failed": 0}
	failed = []
	titles = {r["chat_id"]: r.get("title") for r in frappe.parse_json(doc.recipients or "[]")}
	for job in jobs:
		status = (job.get("status") or "").lower()
		if status in WAITING:
			counts["queued"] += 1
		elif status in SENT:
			counts["sent"] += 1
		elif status in DELIVERED:
			counts["delivered"] += 1
		elif status in READ:
			counts["read"] += 1
		elif status == "failed":
			counts["failed"] += 1
			failed.append(titles.get(job.get("chat_id")) or (job.get("chat_id") or "").split("@")[0])
		if job.get("message_id"):
			frappe.db.sql(
				"""update `tabPeriskope Message` set sent_by = %s where message_id = %s and ifnull(sent_by, '') = ''""",
				(doc.owner, job["message_id"]),
			)
	values = {**counts, "checked_on": now_datetime()}
	if jobs and doc.status in ("Scheduled", "Sending") and not counts["queued"]:
		values["status"] = "Done"
	elif jobs and doc.status == "Scheduled" and counts["queued"] < len(jobs):
		values["status"] = "Sending"
	doc.db_set(values, update_modified=False)
	doc.reload()
	return {"broadcast": _out(doc), "failed": failed[:50]}


@frappe.whitelist(methods=["POST"])
def stop(broadcast: str) -> dict:
	"""Stop the broadcast: the messages not yet sent are cancelled (and their credits returned)."""
	access.require_inbox_user()
	_manager()
	doc = frappe.get_doc(DOCTYPE, broadcast)
	if doc.status not in ("Scheduled", "Sending"):
		frappe.throw(_("This broadcast has already finished."))
	_stop(doc)
	doc.reload()
	return _out(doc)


def _stop(doc):
	result = request("DELETE", f"message/broadcast/{doc.name}", phone=doc.whatsapp_number) or {}
	doc.db_set(
		"status", "Cancelled" if result.get("status") == "deleted" else "Stopped", update_modified=False
	)


def stop_for_test_mode() -> int:
	"""Test mode is on: stop every broadcast still scheduled or sending that has a chat test mode
	does not allow (Periskope would send it anyway, as it does a scheduled message). Returns how
	many were stopped. Never raises."""
	stopped = 0
	for name in frappe.get_all(DOCTYPE, filters={"status": ["in", ["Scheduled", "Sending"]]}, pluck="name"):
		doc = frappe.get_doc(DOCTYPE, name)
		chat_ids = [r.get("chat_id") for r in frappe.parse_json(doc.recipients or "[]")]
		if chat_ids and all(safety.send_mode(c) == safety.LIVE for c in chat_ids):
			continue
		try:
			_stop(doc)
			stopped += 1
		except Exception:
			frappe.log_error(title=f"Periskope: could not stop broadcast {name} for test mode")
	return stopped
