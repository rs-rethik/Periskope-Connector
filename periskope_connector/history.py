"""Copy the last months of every Periskope number's chats into the WhatsApp app, once.

Webhooks only bring messages from the day a number was connected, and the hourly back-fill
(backfill.py) looks back a week at most. This import reads every chat active in the last MONTHS
months and saves the messages the app does not have yet (Settings > Periskope connection > Import
last 6 months, a long background job).

The messages are saved with the history flag (messages.create_message): they are linked to the CRM,
and no AI, auto reply, welcome, contact queue, file copy, alert or SLA clock runs on them. A file is
not downloaded: the inbox fetches it from Periskope when someone opens it (api/inbox.media).
"""

from datetime import datetime, timezone

import frappe
from dateutil.relativedelta import relativedelta
from frappe import _
from frappe.utils import add_to_date, now_datetime

from periskope_connector import backfill
from periskope_connector.messages import create_message

MONTHS = 6
MAX_CHATS = 5000  # per number
MAX_MESSAGE_PAGES = 1000  # per chat: 50,000 messages
OPEN_WITHIN_HOURS = 24  # a chat the import made stays open only if the customer wrote last this recently
JOB_ID = "periskope-history-import"


def queue_import() -> None:
	frappe.enqueue(
		"periskope_connector.history.import_history",
		queue="long",
		timeout=12 * 3600,
		job_id=JOB_ID,
		deduplicate=True,
		enqueue_after_commit=True,  # after "Queued." below, so the job's progress is not overwritten
	)
	# the admin's request commits this at its end
	frappe.db.set_single_value("Periskope Settings", "periskope_import_status", _("Queued."))


def import_history(months: int = MONTHS) -> int:
	"""Save each number's messages of the last `months` months that the app does not have. Returns
	how many were saved. Running it again only adds what is still missing."""
	if not frappe.db.get_single_value("Periskope Settings", "enabled"):
		_status(_("Not started: Periskope is not enabled."))
		return 0
	started = now_datetime()
	cutoff = datetime.now(timezone.utc) - relativedelta(months=months)
	phones = backfill._linked_phones()
	saved = 0
	for phone in phones:
		saved += _import_number(phone, cutoff, saved)
	_settle_new_chats(phones, started)
	_status(
		_("Imported {0} messages of the last {1} months on {2}.").format(
			saved, months, frappe.utils.format_datetime(now_datetime())
		)
	)
	return saved


def _import_number(phone: str, cutoff: datetime, saved_before: int) -> int:
	try:
		chat_ids = backfill._active_chats(phone, cutoff, max_chats=MAX_CHATS)
	except Exception:
		frappe.log_error(title=f"Periskope: history import could not list chats for {phone}")
		return 0
	saved = 0
	for index, chat_id in enumerate(chat_ids, start=1):
		_status(
			_("Importing {0}: chat {1} of {2}, {3} messages saved so far.").format(
				phone, index, len(chat_ids), saved_before + saved
			)
		)
		saved += _import_chat(chat_id, phone, cutoff)
	return saved


def _import_chat(chat_id: str, phone: str, cutoff: datetime) -> int:
	try:
		missing = backfill._missing_messages(chat_id, phone, cutoff, max_pages=MAX_MESSAGE_PAGES)
	except Exception:
		frappe.log_error(title=f"Periskope: history import could not read chat {chat_id}")
		return 0
	saved = 0
	frappe.flags.periskope_history = True
	try:
		for data in missing:  # oldest first, so a rep's reply marks the messages before it as replied
			# One message that cannot be saved must not undo or block the rest of the chat.
			frappe.db.savepoint("periskope_history_message")
			try:
				if create_message({"event": "message.created", "data": data}):
					saved += 1
			except Exception:
				frappe.db.rollback(save_point="periskope_history_message")
				frappe.log_error(
					title=f"Periskope: history import could not save message {data.get('message_id')}"
				)
	finally:
		frappe.flags.periskope_history = False
	frappe.db.commit()  # a long job: each chat's messages are kept even if a later one fails
	return saved


def _settle_new_chats(phones: list[str], started: datetime) -> None:
	"""Chats the import made (no SLA clock ever started on them) are resolved, except where the
	customer wrote last within OPEN_WITHIN_HOURS. Chats that existed before are left as they are."""
	if not phones:
		return
	chat = frappe.qb.DocType("Periskope Chat")
	recent = add_to_date(now_datetime(), hours=-OPEN_WITHIN_HOURS)
	customer_wrote_recently = (chat.last_message_direction == "Incoming") & (chat.last_message_at >= recent)
	(
		frappe.qb.update(chat)
		.set(chat.status, "Resolved")
		.where(
			chat.whatsapp_number.isin(phones)
			& (chat.status == "Open")
			& chat.opened_on.isnull()
			& (chat.creation >= started)
			& ~customer_wrote_recently
		)
	).run()
	frappe.db.commit()  # the last step of the long job


def _status(text: str) -> None:
	frappe.db.set_single_value("Periskope Settings", "periskope_import_status", text)
	frappe.db.commit()  # shown in Settings while the job runs
