"""Scheduled messages, sent by Periskope (POST/GET/PATCH/DELETE /message/scheduled).

A message can be scheduled from the inbox, once or repeating (managers only), on a Periskope number
(Meta numbers are not scheduled by Periskope). Periskope sends it at the time, whether or not this site
is up; the sent message then arrives by webhook like any other. The app keeps a copy of each schedule
(Periskope Scheduled Message) for the chat's list and the Scheduled page:

- sync() (every 15 minutes) reads Periskope's list, so schedules made or changed in the Periskope
  console show too, and reads the runs since the last check: a failed run rings the bell of whoever
  scheduled it, and a sent run's message is marked as theirs;
- test mode: a message can only be scheduled to a test number. Periskope sends without asking this
  site, so when test mode is turned on every schedule to another customer is paused (and marked, so a
  manager can resume it later); sync() pauses any that show up meanwhile.
"""

import base64

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime, now_datetime

from periskope_connector import attachments, meta, safety
from periskope_connector.calls import _utc
from periskope_connector.client import request
from periskope_connector.messages import _to_site_datetime

DOCTYPE = "Periskope Scheduled Message"
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
INTERVALS = ("day", "week", "month")
MAX_TEXT = 4096
MAX_FILE_BYTES = 15 * 1024 * 1024
RUN_CHECKS_PER_SYNC = 50
NOT_FAILURES = {"paused", "skipped"}
RUN_LOOKBACK_DAYS = 3


def _iso(value) -> str:
	return _utc(value).replace(":00Z", ":00.000Z") if value else None


def _repeat_config(repeat: dict | None) -> dict | None:
	if not repeat:
		return None
	interval = repeat.get("interval")
	if interval not in INTERVALS:
		frappe.throw(_("Choose how often it repeats: every day, week or month."))
	config = {"repeat_interval": interval, "repeat_value": max(1, int(repeat.get("value") or 1))}
	days = [d for d in (repeat.get("days") or []) if d in WEEKDAYS]
	if days and interval == "day":
		config["repeat_days"] = days
	if repeat.get("ends"):
		config["repeat_ends"] = _iso(repeat["ends"])
	return config


def _media(file_name: str | None) -> dict | None:
	if not file_name:
		return None
	from periskope_connector.api.compose import media_type

	f = frappe.db.get_value("File", file_name, ["name", "file_name", "file_size"], as_dict=True)
	if not f:
		frappe.throw(_("The file was not found."))
	if (f.file_size or 0) > MAX_FILE_BYTES:
		frappe.throw(_("The file is larger than 15 MB."))
	import mimetypes

	mimetype = mimetypes.guess_type(f.file_name or "")[0] or "application/octet-stream"
	return {
		"type": media_type(mimetype),
		"filedata": base64.b64encode(attachments._read(f.name)).decode(),
		"mimetype": mimetype,
		"filename": f.file_name,
	}


def check_allowed(chat_id: str, number: str | None):
	"""A Periskope number, and in test mode a test number."""
	if not number or meta.is_meta(number):
		frappe.throw(_("Messages can only be scheduled on a Periskope number, not on the Meta number."))
	if safety.send_mode(chat_id) != safety.LIVE:
		frappe.throw(
			_("Test mode is on: messages can only be scheduled to the test numbers (Settings > Test mode).")
		)


def create(chat_row, text: str, when, repeat: dict | None = None, file: str | None = None) -> str:
	"""Schedule a message in Periskope for this chat. Returns the app's copy."""
	text = (text or "").strip()
	if not text and not file:
		frappe.throw(_("Type a message or attach a file."))
	if len(text) > MAX_TEXT:
		frappe.throw(_("The message is longer than {0} characters.").format(MAX_TEXT))
	when = get_datetime(when)
	if when <= add_to_date(now_datetime(), minutes=1):
		frappe.throw(_("Choose a time at least a minute from now."))
	number = chat_row.whatsapp_number or chat_row.org_phone
	check_allowed(chat_row.chat_id, number)
	body = {"chat_id": chat_row.chat_id, "scheduled_at": _iso(when)}
	if text:
		body["message"] = text
	media = _media(file)
	if media:
		body["media"] = media
	config = _repeat_config(repeat)
	if config:
		body.update(is_repeat=True, repeat_config=config)
	made = request("POST", "message/scheduled", phone=number, json=body) or {}
	if not made.get("scheduled_id"):
		frappe.throw(_("Periskope did not schedule the message."))
	name = save(made, scheduled_by=frappe.session.user, file=file, chat=chat_row.name)
	return name


def save(
	item: dict, scheduled_by: str | None = None, file: str | None = None, chat: str | None = None
) -> str:
	"""Save or refresh the app's copy of one of Periskope's scheduled messages."""
	sid = item["scheduled_id"]
	payload = item.get("message_payload") or {}
	config = item.get("repeat_config") or {}
	number = "".join(ch for ch in (item.get("org_phone") or "").split("@")[0] if ch.isdigit())
	chat_id = item.get("chat_id") or ""
	if chat_id and "@" not in chat_id:
		chat_id += "@c.us"
	if not chat:
		from periskope_connector import chat as chat_module

		chat = chat_module.name_of(number, chat_id)
	values = {
		"periskope_chat": chat,
		"chat_id": chat_id,
		"whatsapp_number": number if frappe.db.exists("WhatsApp Number", number) else None,
		"message": payload.get("message") or "",
		"status": {"paused": "Paused", "completed": "Completed"}.get(item.get("status"), "Scheduled"),
		"next_run": _to_site_datetime(item.get("scheduled_at")),
		"skip_next": 1 if item.get("skip_next") else 0,
		"is_repeat": 1 if item.get("is_repeat") else 0,
		"repeat_interval": config.get("repeat_interval") or "",
		"repeat_value": config.get("repeat_value") or 1,
		"repeat_days": ",".join(config.get("repeat_days") or []),
		"repeat_ends": _to_site_datetime(config.get("repeat_ends")),
	}
	if file:
		values["file"] = frappe.db.get_value("File", file, "file_url")
	if frappe.db.exists(DOCTYPE, sid):
		if values["status"] != "Paused":
			values["paused_for_test_mode"] = 0
		_update_changed(sid, values)
	else:
		by = scheduled_by or item.get("performed_by")
		values["scheduled_by"] = by if by and frappe.db.exists("User", by) else None
		values["runs_checked_to"] = now_datetime()
		frappe.get_doc({"doctype": DOCTYPE, "scheduled_id": sid, **values}).insert(ignore_permissions=True)
	return sid


def _update_changed(sid: str, values: dict):
	"""Write only what changed. `modified` then says when the schedule last changed, not when it was
	last read: sync() checks a finished schedule's runs once more after it changed, not for ever."""
	old = frappe.db.get_value(DOCTYPE, sid, list(values), as_dict=True)
	changed = {k: v for k, v in values.items() if _differs(old.get(k), v)}
	if changed:
		frappe.db.set_value(DOCTYPE, sid, changed)


def _differs(old, new) -> bool:
	if old in (None, "") and new in (None, ""):
		return False
	if hasattr(old, "isoformat") and new:
		return get_datetime(old).replace(microsecond=0) != get_datetime(new).replace(microsecond=0)
	return str(old) != str(new)


def refresh(sid: str) -> str | None:
	"""Read one schedule from Periskope again; a schedule Periskope no longer has is removed."""
	item = request("GET", f"message/scheduled/{sid}", missing_ok=True)
	if not item:
		frappe.delete_doc(DOCTYPE, sid, ignore_permissions=True, force=True)
		return None
	return save(item.get("scheduled_message") or item)


def change(sid: str, changes: dict) -> str | None:
	"""Edit a schedule in Periskope (text, time, repeat, paused, skip_next), then read it back."""
	row = frappe.get_doc(DOCTYPE, sid)
	if row.status == "Completed":
		frappe.throw(_("This scheduled message has finished; it can no longer be changed."))
	body = {}
	if "text" in changes:
		text = (changes["text"] or "").strip()
		if not text and not row.file:
			frappe.throw(_("Type a message."))
		body["message"] = text
	if changes.get("when"):
		when = get_datetime(changes["when"])
		if when <= add_to_date(now_datetime(), minutes=1):
			frappe.throw(_("Choose a time at least a minute from now."))
		body["scheduled_at"] = _iso(when)
	if "repeat" in changes:
		config = _repeat_config(changes["repeat"])
		body.update(is_repeat=bool(config), repeat_config=config)
	if "paused" in changes:
		if not changes["paused"]:
			check_allowed(row.chat_id, row.whatsapp_number)  # never resumed to a customer in test mode
		body["paused"] = bool(changes["paused"])
	if changes.get("skip_next"):
		body["skip_next"] = True
	if body:
		request("PATCH", f"message/scheduled/{sid}", json=body)
	return refresh(sid)


def delete(sid: str):
	request("DELETE", f"message/scheduled/{sid}", missing_ok=True)
	frappe.delete_doc(DOCTYPE, sid, ignore_permissions=True, force=True)


# --- Sync -----------------------------------------------------------------------------------------


def run():
	"""Scheduled: Periskope's list, the runs since the last check, and the test-mode pause."""
	if not frappe.db.get_single_value("Periskope Settings", "enabled") or not frappe.db.get_single_value(
		"Periskope Settings", "base_url"
	):
		return
	try:
		sync()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Periskope: could not read the scheduled messages")


def sync() -> dict:
	data = request("GET", "message/scheduled", params={"limit": 1000, "sort_order": "asc"}) or {}
	items = data.get("scheduled_messages") or []
	seen = set()
	for item in items:
		if not item.get("scheduled_id"):
			continue
		# Periskope has it, saved here or not: a schedule that could not be saved keeps its row.
		seen.add(item["scheduled_id"])
		try:
			save(item)
		except Exception:
			frappe.log_error(title=f"Periskope: could not save scheduled message {item.get('scheduled_id')}")
	# The whole list was read: what is not in it was deleted in Periskope. An empty answer is not
	# trusted (an outage or a changed reply would wipe every schedule); refresh() removes one by one.
	if items and len(items) < 1000:
		for sid in frappe.get_all(DOCTYPE, filters={"name": ["not in", list(seen)]}, pluck="name"):
			check_runs(sid)
			frappe.delete_doc(DOCTYPE, sid, ignore_permissions=True, force=True)
	frappe.db.commit()
	checked = 0
	# running schedules, and finished ones not checked since they finished; least recently checked first
	row = frappe.qb.DocType(DOCTYPE)
	due = (
		frappe.qb.from_(row)
		.select(row.name)
		.where(
			(row.status != "Completed") | row.runs_checked_to.isnull() | (row.runs_checked_to < row.modified)
		)
		.orderby(row.runs_checked_to)
		.limit(RUN_CHECKS_PER_SYNC)
	).run(pluck=True)
	for sid in due:
		check_runs(sid)
		checked += 1
	paused = pause_for_test_mode() if safety.test_mode() else 0
	frappe.db.commit()
	return {"schedules": len(seen), "runs_checked": checked, "paused": paused}


def check_runs(sid: str):
	"""The runs since the last check: a failure rings the scheduler's bell; a sent message is marked
	as sent by them (Periskope records the API as its sender)."""
	row = frappe.db.get_value(
		DOCTYPE, sid, ["name", "runs_checked_to", "scheduled_by", "periskope_chat"], as_dict=True
	)
	if not row:
		return
	now = now_datetime()
	params = {"sort_order": "asc", "limit": 100}
	if row.runs_checked_to:
		params["start_time"] = _iso(row.runs_checked_to)
	try:
		data = request("GET", f"message/scheduled/{sid}/occurrences", params=params, missing_ok=True) or {}
	except Exception:
		frappe.log_error(title=f"Periskope: could not read the runs of scheduled message {sid}")
		return
	runs = data.get("occurrences") or []
	failure = None
	for run in runs:
		if run.get("is_success") is True and run.get("message_id") and row.scheduled_by:
			_mark_sent_by(run["message_id"], row)
		elif run.get("is_success") is False and (run.get("remarks") or "") not in NOT_FAILURES:
			failure = run.get("remarks") or _("not sent")
	values = {"runs_checked_to": now}
	if failure:
		values["last_failure"] = failure
		_bell(row, failure)
	frappe.db.set_value(DOCTYPE, sid, values, update_modified=False)


def _mark_sent_by(message_id: str, row):
	"""Mark the message a run sent as sent by whoever scheduled it. Found by its exact id (the unique
	index); a run that gives only the WhatsApp id inside it is matched within the schedule's own chat
	(else the last days' messages), never by scanning the whole message table."""
	msg = frappe.qb.DocType("Periskope Message")
	unmarked = msg.sent_by.isnull() | (msg.sent_by == "")
	query = frappe.qb.update(msg).set(msg.sent_by, row.scheduled_by)
	name = frappe.db.get_value("Periskope Message", {"message_id": message_id})
	if name:
		query.where((msg.name == name) & unmarked).run()
		return
	scope = (
		(msg.periskope_chat == row.periskope_chat)
		if row.periskope_chat
		else (msg.creation >= add_to_date(now_datetime(), days=-RUN_LOOKBACK_DAYS))
	)
	query.where(scope & msg.message_id.like(f"%{message_id}%") & unmarked).run()


def _bell(row, reason: str):
	if not row.scheduled_by or not row.periskope_chat:
		return
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"type": "Alert",
			"for_user": row.scheduled_by,
			"subject": _("A scheduled WhatsApp message was not sent ({0})").format(reason)[:140],
			"document_type": "Periskope Chat",
			"document_name": row.periskope_chat,
		}
	).insert(ignore_permissions=True)


def pause_for_test_mode() -> int:
	"""Pause every running schedule to a customer test mode does not allow (Periskope would send it
	anyway). Marked, so a manager can resume it once test mode is off."""
	paused = 0
	for row in frappe.get_all(DOCTYPE, filters={"status": "Scheduled"}, fields=["name", "chat_id"]):
		if safety.send_mode(row.chat_id) == safety.LIVE:
			continue
		try:
			request("PATCH", f"message/scheduled/{row.name}", json={"paused": True})
			frappe.db.set_value(DOCTYPE, row.name, {"status": "Paused", "paused_for_test_mode": 1})
			paused += 1
		except Exception:
			frappe.log_error(title=f"Periskope: could not pause scheduled message {row.name} for test mode")
	return paused


def on_test_mode_on():
	"""Periskope Settings saved with test mode turned on: what Periskope would still send by itself
	is held back, the scheduled messages (paused) and the broadcasts (stopped)."""
	from periskope_connector.api import broadcast

	broadcast.stop_for_test_mode()
	try:
		sync()
	except Exception:
		frappe.log_error(title="Periskope: could not pause the scheduled messages for test mode")
