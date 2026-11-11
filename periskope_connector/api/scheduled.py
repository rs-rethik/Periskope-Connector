"""Scheduled messages from the inbox (scheduled.py): a chat's list, schedule, edit, pause, skip the next
run, delete, the runs, and every schedule for the Scheduled page.

Anyone who may write in a chat may schedule a one-time message there; repeating messages are for
managers. A schedule is changed by whoever made it, or by a manager. The Scheduled page is for managers.
"""

import frappe
from frappe import _

from periskope_connector import access, meta, scheduled
from periskope_connector.api.compose import _chat, _own_file
from periskope_connector.api.permission import require_manager
from periskope_connector.client import request
from periskope_connector.messages import _to_site_datetime

FIELDS = [
	"name",
	"periskope_chat",
	"chat_id",
	"whatsapp_number",
	"message",
	"file",
	"status",
	"next_run",
	"scheduled_by",
	"paused_for_test_mode",
	"skip_next",
	"is_repeat",
	"repeat_interval",
	"repeat_value",
	"repeat_days",
	"repeat_ends",
	"last_failure",
]


def _decorate(rows: list) -> list:
	me, manager = frappe.session.user, access.is_manager()
	names = {u: frappe.utils.get_fullname(u) for u in {r.scheduled_by for r in rows if r.scheduled_by}}
	for r in rows:
		r.scheduled_by_name = names.get(r.scheduled_by) or ""
		r.can_edit = manager or (r.scheduled_by == me and not r.is_repeat)
		r.repeat_days = [d for d in (r.repeat_days or "").split(",") if d]
	return rows


def _editable(name: str) -> frappe._dict:
	row = frappe.db.get_value(
		DOCTYPE, name, ["name", "periskope_chat", "scheduled_by", "is_repeat"], as_dict=True
	)
	if not row or not row.periskope_chat or not access.can_see_chat(row.periskope_chat):
		frappe.throw(_("You cannot change this scheduled message."), frappe.PermissionError)
	if not access.is_manager() and (row.scheduled_by != frappe.session.user or row.is_repeat):
		frappe.throw(_("Only who scheduled it, or a manager, can change it."), frappe.PermissionError)
	return row


DOCTYPE = scheduled.DOCTYPE


@frappe.whitelist()
def for_chat(chat: str) -> dict:
	"""The chat's pending schedules, and what the person may do there."""
	access.require_inbox_user()
	row = _chat(chat)
	number = row.whatsapp_number or row.org_phone
	rows = frappe.get_all(
		DOCTYPE,
		filters={"periskope_chat": chat, "status": ["!=", "Completed"]},
		fields=FIELDS,
		order_by="next_run asc",
	)
	return {
		"scheduled": _decorate(rows),
		"can_schedule": bool(number) and not meta.is_meta(number),
		"can_repeat": access.is_manager(),
	}


def _repeat(repeat) -> dict | None:
	repeat = frappe.parse_json(repeat) if isinstance(repeat, str) else repeat
	if repeat and not access.is_manager():
		frappe.throw(_("Only managers can schedule repeating messages."), frappe.PermissionError)
	return repeat or None


@frappe.whitelist(methods=["POST"])
def schedule(
	chat: str, when: str, text: str = "", repeat: str | dict | None = None, file: str | None = None
) -> dict:
	"""Schedule a message in the chat, once or (managers) repeating."""
	access.require_inbox_user()
	row = _chat(chat)
	repeat = _repeat(repeat)
	if file:
		_own_file(file)
	name = scheduled.create(row, text, when, repeat, file)
	return _decorate(frappe.get_all(DOCTYPE, filters={"name": name}, fields=FIELDS))[0]


@frappe.whitelist(methods=["POST"])
def update(
	name: str,
	text: str | None = None,
	when: str | None = None,
	repeat: str | dict | None = None,
	paused: int | str | None = None,
	skip_next: int | str | None = None,
) -> dict | None:
	"""Change a schedule: text, time, repeat (managers), pause or resume, or skip the next run."""
	access.require_inbox_user()
	_editable(name)
	changes = {}
	if text is not None:
		changes["text"] = text
	if when:
		changes["when"] = when
	if repeat is not None:
		changes["repeat"] = _repeat(repeat)
	if paused is not None and paused != "":
		changes["paused"] = bool(frappe.utils.cint(paused))
	if frappe.utils.cint(skip_next):
		changes["skip_next"] = True
	sid = scheduled.change(name, changes)
	rows = frappe.get_all(DOCTYPE, filters={"name": sid}, fields=FIELDS) if sid else []
	return _decorate(rows)[0] if rows else None


@frappe.whitelist(methods=["POST"])
def delete(name: str) -> None:
	access.require_inbox_user()
	_editable(name)
	scheduled.delete(name)


@frappe.whitelist()
def runs(name: str) -> list[dict]:
	"""The schedule's past runs, newest first, from Periskope."""
	access.require_inbox_user()
	row = frappe.db.get_value(DOCTYPE, name, ["periskope_chat"], as_dict=True)
	if not row or not row.periskope_chat or not access.can_see_chat(row.periskope_chat):
		frappe.throw(_("You cannot open this scheduled message."), frappe.PermissionError)
	data = (
		request("GET", f"message/scheduled/{name}/occurrences", params={"limit": 50}, missing_ok=True) or {}
	)
	out = []
	for run in data.get("occurrences") or []:
		ok = run.get("is_success")
		out.append(
			{
				"at": _to_site_datetime(run.get("created_at")),
				"result": "sent"
				if ok is True
				else "pending"
				if ok is None
				else (run.get("remarks") or "not sent"),
				"ok": ok,
			}
		)
	return out


@frappe.whitelist()
def all_scheduled(
	status: str | None = None, number: str | None = None, repeat: str | None = None
) -> list[dict]:
	"""Every schedule, for the Scheduled page (managers)."""
	access.require_inbox_user()
	require_manager()
	if not access.is_manager():
		frappe.throw(_("This page is for managers."), frappe.PermissionError)
	filters = {}
	if status in ("Scheduled", "Paused", "Completed"):
		filters["status"] = status
	if number:
		filters["whatsapp_number"] = number
	if repeat in ("0", "1"):
		filters["is_repeat"] = int(repeat)
	rows = frappe.get_all(DOCTYPE, filters=filters, fields=FIELDS, order_by="next_run asc", limit=500)
	titles = dict(
		frappe.get_all(
			"Periskope Chat",
			filters={"name": ["in", [r.periskope_chat for r in rows if r.periskope_chat] or [""]]},
			fields=["name", "chat_name"],
			as_list=True,
		)
	)
	for r in rows:
		r.chat_title = titles.get(r.periskope_chat) or (r.chat_id or "").split("@")[0]
	return _decorate(rows)
