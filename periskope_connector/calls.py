"""WhatsApp calls on the company numbers, read from Periskope's calls API (GET /calls, GET /calls/{id}).

Periskope's API only reads calls: they are placed and answered in the Periskope app or on the phone.
Every few minutes sync() saves the new and changed calls as Periskope Call rows:

- each call joins its chat (the customer on that number), and shows in the chat's conversation in
  the inbox, with its result, length, who took it, and for calls taken in Periskope the AI summary,
  the transcript and the recording (played from Periskope, api/calls.py);
- an incoming call from a number with no chat yet starts one, as a first message would;
- a missed incoming call puts a bell on the chat's assignees (else the number's rep);
- when the chat is linked to a lead or deal, the call goes into the CRM as a CRM Call Log (its Calls
  tab), with the summary and transcript as its note. Test mode applies as for every CRM write.
"""

from datetime import timedelta, timezone
from zoneinfo import ZoneInfo

import frappe
from frappe import _
from frappe.utils import add_to_date, escape_html, get_datetime, get_system_timezone, now_datetime

from periskope_connector import chat, crm_context, numbers, safety
from periskope_connector.client import request
from periskope_connector.messages import _to_site_datetime
from periskope_connector.system import insert_as_system, save_as_system

DOCTYPE = "Periskope Call"
PAGE = 100
MAX_PAGES = 20
FIRST_DAYS = 7  # how far back the first sync reads
OVERLAP = timedelta(hours=1)  # read again, for results and summaries that come late
PENDING_DAYS = 2  # how long a call's transcript is waited for
ALERT_HOURS = 12  # an older missed call (a first sync, a long outage) gets no bell
NOT_ANSWERED = {"missed", "rejected", "unavailable", "cancelled", "abandoned"}
LIVE_RESULTS = {"ongoing", "upcoming"}
CRM_STATUS = {
	"connected": "Completed",
	"acceptedelsewhere": "Completed",
	"missed": "No Answer",
	"unavailable": "No Answer",
	"rejected": "Busy",
	"cancelled": "Canceled",
	"abandoned": "Canceled",
	"failed": "Failed",
	"invalid": "Failed",
	"ongoing": "In Progress",
	"upcoming": "Initiated",
}


def _enabled() -> bool:
	return bool(frappe.db.get_single_value("Periskope Settings", "enabled"))


def sync() -> dict:
	"""Save the calls since the last sync (scheduled). Returns what it did."""
	if not _enabled():
		return {"saved": 0}
	since = _since()
	saved = offset = 0
	for _page in range(MAX_PAGES):
		data = request(
			"GET",
			"calls",
			params={"start_time": _utc(since), "sort_order": "asc", "limit": PAGE, "offset": offset},
		)
		calls = (data or {}).get("calls") or []
		for call in calls:
			try:
				frappe.db.savepoint("periskope_call")
				if save(call):
					saved += 1
			except Exception:
				frappe.db.rollback(save_point="periskope_call")
				frappe.log_error(title=f"Periskope: could not save call {call.get('call_id')}")
		frappe.db.commit()
		if len(calls) < PAGE:
			break
		offset += PAGE
	return {"saved": saved, "since": str(since)}


def run():
	"""The scheduled sync: quiet when Periskope is not set up, errors logged once per run."""
	if not _enabled() or not frappe.db.get_single_value("Periskope Settings", "base_url"):
		return
	try:
		sync()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Periskope: could not read the calls")


def _since():
	"""From the newest call saved (less an hour), or the oldest call still waiting for its
	transcript, whichever is earlier. The first sync reads FIRST_DAYS back."""
	newest = frappe.get_all(DOCTYPE, pluck="call_time", order_by="call_time desc", limit=1)
	if not newest or not newest[0]:
		return add_to_date(now_datetime(), days=-FIRST_DAYS)
	start = get_datetime(newest[0]) - OVERLAP
	pending = frappe.get_all(
		DOCTYPE,
		filters={
			"transcript_status": "pending",
			"call_time": [">", add_to_date(now_datetime(), days=-PENDING_DAYS)],
		},
		pluck="call_time",
		order_by="call_time asc",
		limit=1,
	)
	pending = pending[0] if pending else None
	if pending and get_datetime(pending) < start:
		start = get_datetime(pending)
	return start


def _utc(value) -> str:
	"""A site time as Periskope's start_time (UTC, minute precision)."""
	local = get_datetime(value).replace(tzinfo=ZoneInfo(get_system_timezone()))
	return local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")


def save(call: dict) -> str | None:
	"""Save or refresh one call from Periskope. Returns the row's name."""
	call_id = call.get("call_id")
	if not call_id:
		return None
	number = numbers.digits(call.get("org_phone"))
	chat_id = call.get("chat_id") or ""
	direction = {"incoming": "Incoming", "outgoing": "Outgoing"}.get(call.get("direction"), "")
	result = (call.get("result") or "").lower()
	call_time = _to_site_datetime(call.get("timestamp")) or now_datetime()
	recording = call.get("recording") or {}
	values = {
		"whatsapp_number": number if frappe.db.exists("WhatsApp Number", number) else None,
		"chat_id": chat_id,
		"chat_name": call.get("chat_name") or "",
		"direction": direction,
		"result": result,
		"call_time": call_time,
		"duration": int(call.get("duration_seconds") or 0),
		"handled_via": call.get("handled_via") or "",
		"answered_by": call.get("answered_by") or "",
		"summary": (call.get("summary") or {}).get("text") or "",
		"transcript_status": (call.get("transcript") or {}).get("status") or "",
		"has_recording": 1 if recording.get("url") else 0,
		"recording_duration": int(recording.get("duration_seconds") or 0),
	}
	existing = frappe.db.get_value(
		DOCTYPE, call_id, ["name", "periskope_chat", "transcript", "alerted"], as_dict=True
	)
	values["periskope_chat"] = (existing and existing.periskope_chat) or _chat_of(
		number, chat_id, direction, values
	)
	if values["transcript_status"] == "done" and not (existing and existing.transcript):
		values.update(_details(call_id))
	changed = not existing
	if existing:
		values = {k: v for k, v in values.items() if v not in (None, "") or k == "periskope_chat"}
		before = frappe.db.get_value(DOCTYPE, call_id, list(values), as_dict=True) or {}
		changed = any(str(before.get(k) or "") != str(v or "") for k, v in values.items())
		if changed:
			frappe.db.set_value(DOCTYPE, call_id, values, update_modified=False)
	else:
		insert_as_system({"doctype": DOCTYPE, "call_id": call_id, **values})
	row = frappe.get_doc(DOCTYPE, call_id)
	if row.has_recording and not row.recording_file and result not in LIVE_RESULTS:
		frappe.enqueue(
			"periskope_connector.calls.keep_recording",
			call=call_id,
			queue="long",
			job_id=f"periskope_call_recording::{call_id}",
			deduplicate=True,
			enqueue_after_commit=True,
		)
	if not row.alerted and _should_alert(row):
		_alert(row)
	if row.periskope_chat and result not in LIVE_RESULTS:
		to_crm(row)
	if row.periskope_chat and changed:
		# the calls job re-reads the last hour every 5 minutes: only a new or changed call reloads inboxes
		from periskope_connector import inbox_realtime

		inbox_realtime.chat_changed(row.periskope_chat)
	return call_id


MAX_RECORDING_BYTES = 50 * 1024 * 1024


def keep_recording(call: str) -> str | None:
	"""Background job: save the call's recording as a private File on the call, so it plays after
	Periskope's link (which expires) is gone. Returns the File name."""
	from periskope_connector import attachments

	row = frappe.db.get_value(DOCTYPE, call, ["name", "has_recording", "recording_file"], as_dict=True)
	if not row or not row.has_recording or row.recording_file:
		return row and row.recording_file
	try:
		url = ((request("GET", f"calls/{call}") or {}).get("recording") or {}).get("url")
		if not url:
			return None
		content = attachments.download(url, MAX_RECORDING_BYTES)
		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"call-{call}.wav",
				"content": content,
				"is_private": 1,
				"attached_to_doctype": DOCTYPE,
				"attached_to_name": call,
			}
		)
		file.flags.ignore_permissions = True
		file.insert()
	except Exception:
		frappe.log_error(title=f"Periskope: could not keep the recording of call {call}")
		return None
	frappe.db.set_value(DOCTYPE, call, "recording_file", file.name, update_modified=False)
	return file.name


def _details(call_id: str) -> dict:
	"""The transcript (and summary) of a finished call, from Get a call."""
	try:
		full = request("GET", f"calls/{call_id}") or {}
	except Exception:
		frappe.log_error(title=f"Periskope: could not read call {call_id}")
		return {}
	lines = [
		f"{u.get('speaker') or ''}: {u.get('text') or ''}".strip()
		for u in (full.get("transcript") or {}).get("utterances") or []
	]
	out = {"transcript": "\n".join(lines)}
	if (full.get("summary") or {}).get("text"):
		out["summary"] = full["summary"]["text"]
	return out


def _chat_of(number: str, chat_id: str, direction: str, values: dict) -> str | None:
	"""The chat of the customer on that number. An incoming call from someone with no chat yet starts
	one (as a first message does: assignment rules, territory rep). Groups and unresolved callers
	(@lid: WhatsApp hides their number) get none. A group call joins the group's chat when it has one."""
	if not chat_id or chat_id.endswith("@lid"):
		return None
	if chat_id.endswith("@g.us"):
		return chat.name_of(number, chat_id)
	name = chat.name_of(number, chat_id)
	if name or direction != "Incoming" or not values.get("whatsapp_number"):
		return name
	name = chat._insert_chat(
		{
			"chat_id": chat_id,
			"status": "Open",
			"is_group": 0,
			"contact_number": numbers.digits(chat_id),
			"org_phone": number,
			"whatsapp_number": number,
			"chat_name": values.get("chat_name") or None,
			"last_message_at": values["call_time"],
			"last_incoming_at": values["call_time"],
			"last_message_preview": _("Missed call")
			if values["result"] in NOT_ANSWERED
			else _("WhatsApp call"),
			"last_message_direction": "Incoming",
		}
	)
	chat._lifecycle("new_chat", name, values["call_time"])
	return name


# --- Missed calls -----------------------------------------------------------------------------------


def _should_alert(row) -> bool:
	return (
		row.direction == "Incoming"
		and row.result in NOT_ANSWERED
		and bool(row.periskope_chat)
		and get_datetime(row.call_time) > add_to_date(now_datetime(), hours=-ALERT_HOURS)
	)


def _alert(row):
	"""A bell for the chat's assignees, else the number's rep: a customer tried to call."""
	assigned = frappe.parse_json(frappe.db.get_value("Periskope Chat", row.periskope_chat, "_assign") or "[]")
	users = [u for u in assigned if u] or [numbers.rep(row.whatsapp_number)]
	who = row.chat_name or (f"+{numbers.digits(row.chat_id)}" if row.chat_id else _("a customer"))
	for user in {u for u in users if u and frappe.db.get_value("User", u, "enabled")}:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"type": "Alert",
				"for_user": user,
				"subject": _("Missed WhatsApp call from {0}").format(escape_html(who))[:140],
				"document_type": "Periskope Chat",
				"document_name": row.periskope_chat,
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value(DOCTYPE, row.name, "alerted", 1, update_modified=False)


# --- CRM --------------------------------------------------------------------------------------------


def _phone(digits: str) -> str:
	return f"+{digits}" if digits else ""


def _user(email: str | None) -> str | None:
	return email if email and frappe.db.exists("User", {"name": email, "enabled": 1}) else None


def to_crm(row) -> str | None:
	"""The call as a CRM Call Log on the chat's lead or deal (the CRM's Calls tab), with the summary
	and transcript as its note. Written again only when the call changed. Test mode: a dry run."""
	from periskope_connector import writeback

	record = crm_context.record_of(row.chat_id)
	if not record or not frappe.db.exists("DocType", "CRM Call Log"):
		return None
	customer, ours = _phone(numbers.digits(row.chat_id)), _phone(numbers.digits(row.whatsapp_number))
	incoming = row.direction == "Incoming"
	start = get_datetime(row.call_time)
	values = {
		"type": "Incoming" if incoming else "Outgoing",
		"status": CRM_STATUS.get(row.result, "Completed"),
		"from": customer if incoming else ours,
		"to": ours if incoming else customer,
		"start_time": start,
		"end_time": start + timedelta(seconds=row.duration or 0),
		"duration": row.duration or 0,
		"medium": "WhatsApp",
		"receiver": _user(row.answered_by) if incoming else None,
		"caller": None if incoming else _user(row.answered_by),
		"reference_doctype": record[0],
		"reference_docname": record[1],
	}
	key = f"call:{row.call_id}"
	note_text = _note_text(row)
	digest = writeback._hash(str(sorted(values.items(), key=lambda kv: kv[0])), note_text)
	tracked = writeback._tracked(key)
	if tracked and tracked.content_hash == digest and frappe.db.exists("CRM Call Log", tracked.target_name):
		return tracked.target_name

	def write():
		if note_text:
			values["note"] = writeback._upsert_note(
				row.chat_id, f"call-note:{row.call_id}", "Note", _("WhatsApp call"), note_text
			)
		if tracked and frappe.db.exists("CRM Call Log", tracked.target_name):
			log = frappe.get_doc("CRM Call Log", tracked.target_name)
			log.update(values)
			save_as_system(log)
		else:
			log = insert_as_system(
				{"doctype": "CRM Call Log", "id": f"periskope-{row.call_id}"[:140], **values}
			)
		writeback._track(key, "Call", row.chat_id, record, "CRM Call Log", log.name, digest)
		frappe.db.set_value(DOCTYPE, row.name, "crm_call_log", log.name, update_modified=False)
		return log.name

	return safety.crm_write(
		row.chat_id,
		"Call Log",
		_("{0} WhatsApp call ({1})").format(values["type"], row.result or "-"),
		write,
		once=key,  # the sync reads the same call every 5 minutes: one dry-run row per call
		target_doctype="CRM Call Log",
		**writeback._ref(record),
	)


def _note_text(row) -> str:
	if not (row.summary or row.transcript):
		return ""
	parts = []
	if row.summary:
		parts.append(f"<p><b>{_('Summary')}</b><br>{escape_html(row.summary)}</p>")
	if row.transcript:
		lines = "<br>".join(escape_html(line) for line in row.transcript.splitlines())
		parts.append(f"<p><b>{_('Transcript')}</b><br>{lines}</p>")
	return "".join(parts)
