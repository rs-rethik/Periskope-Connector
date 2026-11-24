"""A person's own WhatsApp data, for a privacy request (India's DPDP Act): an export of everything the
app keeps about one number, and erasing it.

Erasing removes what this app holds for the number: its 1-to-1 chats and their messages, files,
calls (and kept recordings), notes and activity, the messages it wrote in groups, its contact-queue
row, auto replies and reminders, the timeline comments, notes and tasks the agent wrote from them,
and the webhook logs that carry it. The CRM lead or deal stays: it is the company's sales record;
erase it in the CRM if the request covers it. One Periskope Erasure Log row records who erased how
much and why, without the number. Managers only.
"""

import csv
import hashlib
import io
import zipfile

import frappe
from frappe import _

from periskope_connector import access
from periskope_connector.api.permission import require_manager
from periskope_connector.linking import with_country_code

MESSAGE_COLUMNS = [
	"message_timestamp",
	"direction",
	"chat_name",
	"message_type",
	"body",
	"transcript",
	"media_filename",
]


def _manager():
	"""Managers only (checked on the roles themselves: frappe.only_for is skipped in tests)."""
	access.require_inbox_user()
	require_manager()
	if not access.is_manager():
		frappe.throw(_("Only a manager can export or erase a customer's data."), frappe.PermissionError)


def _number(number: str) -> str:
	full = with_country_code(number)
	if len(full) < 10:
		frappe.throw(_("Enter the WhatsApp number with the country code."))
	return full


def _scope(full: str) -> frappe._dict:
	"""Everything of the number: its 1-to-1 chats and the messages it sent anywhere (groups too)."""
	chat_id = f"{full}@c.us"
	# a manager given numbers exports or erases only what is on their numbers
	allowed = access.allowed_numbers()
	on_mine = {"whatsapp_number": ["in", allowed or [""]]} if allowed is not None else {}
	chats = frappe.get_all("Periskope Chat", filters={"chat_id": chat_id, **on_mine}, pluck="name")
	messages = frappe.get_all(
		"Periskope Message",
		filters=on_mine,
		or_filters=[["chat_id", "=", chat_id], ["sender_phone", "=", full]],
		pluck="name",
	)
	return frappe._dict(chat_id=chat_id, chats=chats, messages=messages)


@frappe.whitelist(methods=["GET", "POST"])
def summary(number: str) -> dict:
	"""How much the app keeps for the number (shown before an export or an erase)."""
	access.require_inbox_user()
	access.require_inbox_user()
	_manager()
	full = _number(number)
	scope = _scope(full)
	return {
		"number": full,
		"chats": len(scope.chats),
		"messages": len(scope.messages),
		"calls": frappe.db.count("Periskope Call", {"chat_id": scope.chat_id}),
		"lead_or_deal": bool(
			frappe.db.exists("CRM Lead", {"periskope_phone_e164": full})
			or frappe.db.exists("CRM Deal", {"periskope_phone_e164": full})
		),
	}


@frappe.whitelist(methods=["GET"])
def export(number: str):
	"""A zip with the number's messages and calls (CSV) and the files they carried."""
	access.require_inbox_user()
	access.require_inbox_user()
	_manager()
	full = _number(number)
	scope = _scope(full)
	rows = frappe.get_all(
		"Periskope Message",
		filters={"name": ["in", scope.messages or [""]]},
		fields=[*MESSAGE_COLUMNS, "attachment_file"],
		order_by="message_timestamp asc",
	)
	calls = frappe.get_all(
		"Periskope Call",
		filters={"chat_id": scope.chat_id},
		fields=["call_time", "direction", "result", "duration", "summary", "transcript"],
		order_by="call_time asc",
	)
	from periskope_connector import attachments

	buffer = io.BytesIO()
	with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
		zf.writestr("messages.csv", _csv(MESSAGE_COLUMNS, rows))
		zf.writestr(
			"calls.csv",
			_csv(["call_time", "direction", "result", "duration", "summary", "transcript"], calls),
		)
		for r in rows:
			if r.attachment_file:
				try:
					zf.writestr(
						f"files/{r.attachment_file}-{r.media_filename or 'file'}",
						attachments._read(r.attachment_file),
					)
				except Exception:
					continue  # a file gone from disk is left out of the export
	frappe.local.response.filename = f"whatsapp-{full}.zip"
	frappe.local.response.filecontent = buffer.getvalue()
	frappe.local.response.type = "download"


def _csv(columns: list[str], rows: list) -> str:
	out = io.StringIO()
	writer = csv.writer(out)
	writer.writerow(columns)
	for r in rows:
		writer.writerow([r.get(c) if r.get(c) is not None else "" for c in columns])
	return out.getvalue()


@frappe.whitelist(methods=["POST"])
def erase(number: str, reason: str, confirm: str) -> dict:
	"""Erase the number's WhatsApp data (see the module notes). `confirm` must repeat the number's last
	four digits. Returns what was erased."""
	access.require_inbox_user()
	access.require_inbox_user()
	_manager()
	full = _number(number)
	if (confirm or "").strip() != full[-4:]:
		frappe.throw(_("Type the last four digits of the number to confirm."))
	if not (reason or "").strip():
		frappe.throw(_("Say why the data is erased (for example: the customer asked on 12 Nov)."))
	scope = _scope(full)
	counts = {}

	def drop(doctype: str, names: list[str]):
		for name in names:
			frappe.delete_doc(doctype, name, ignore_permissions=True, force=True, delete_permanently=True)
		counts[doctype] = counts.get(doctype, 0) + len(names)

	# what the agent wrote into the CRM from these chats: its notes, tasks and timeline comments
	records = frappe.get_all(
		"Periskope CRM Record",
		filters={"chat_id": scope.chat_id},
		fields=["name", "target_doctype", "target_name"],
	)
	for rec in records:
		if rec.target_doctype in ("FCRM Note", "CRM Task", "Comment") and frappe.db.exists(
			rec.target_doctype, rec.target_name
		):
			drop(rec.target_doctype, [rec.target_name])
	drop("Periskope CRM Record", [r.name for r in records])
	comments = frappe.get_all(
		"Periskope Message",
		filters={"name": ["in", scope.messages or [""]], "timeline_comment": ["is", "set"]},
		pluck="timeline_comment",
	)
	drop("Comment", [c for c in comments if frappe.db.exists("Comment", c)])
	files = frappe.get_all(
		"File",
		filters={
			"attached_to_doctype": "Periskope Message",
			"attached_to_name": ["in", scope.messages or [""]],
		},
		pluck="name",
	)
	calls = frappe.get_all("Periskope Call", filters={"chat_id": scope.chat_id}, pluck="name")
	files += frappe.get_all(
		"File",
		filters={"attached_to_doctype": "Periskope Call", "attached_to_name": ["in", calls or [""]]},
		pluck="name",
	)
	drop("File", files)
	drop("Periskope Call", calls)
	for doctype, field in (
		("Periskope Chat Activity", "chat"),
		("Periskope Chat Comment", "chat"),
		("Periskope Chat Seen", "chat"),
		("Periskope Scheduled Message", "periskope_chat"),
	):
		drop(doctype, frappe.get_all(doctype, filters={field: ["in", scope.chats or [""]]}, pluck="name"))
	for doctype in ("Periskope Auto Reply", "Periskope Reply Reminder", "Periskope Agent Action"):
		drop(doctype, frappe.get_all(doctype, filters={"chat_id": scope.chat_id}, pluck="name"))
	drop("Periskope Message", scope.messages)
	drop("Periskope Chat", scope.chats)
	drop("Periskope Contact", frappe.get_all("Periskope Contact", filters={"phone": full}, pluck="name"))
	# the raw webhook bodies that carry the number (kept for debugging)
	log = frappe.qb.DocType("Periskope Webhook Log")
	logs = (frappe.qb.from_(log).select(log.name).where(log.payload.like(f"%{full}@%")).limit(5000)).run(
		pluck=True
	)
	if logs:
		frappe.qb.from_(log).delete().where(log.name.isin(logs)).run()  # no hooks or children
	counts["Periskope Webhook Log"] = len(logs)

	frappe.get_doc(
		{
			"doctype": "Periskope Erasure Log",
			"number_masked": f"…{full[-4:]}",
			"number_hash": hashlib.sha256(full.encode()).hexdigest(),
			"erased_by": frappe.session.user,
			"reason": reason.strip()[:500],
			"counts": frappe.as_json({k: v for k, v in counts.items() if v}),
		}
	).insert(ignore_permissions=True)  # the audit row is the app's own record
	return {k: v for k, v in counts.items() if v}
