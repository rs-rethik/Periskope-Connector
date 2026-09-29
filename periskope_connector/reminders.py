"""Remind reps when a customer is waiting for a WhatsApp reply.

Runs every 10 minutes. Per chat (not per message):
1. A customer message that needs a reply and has no later rep message starts the clock.
2. Only working hours count (site time, e.g. 09:00-19:00, Monday-Saturday), so a message sent
   at night starts counting the next morning and nothing fires outside working hours.
3. After `remind_after_hours` a ToDo is created for the rep, once per waiting chat.
4. If the chat is still unanswered `escalate_after_hours` later, managers are emailed.
5. When the rep replies, the reminder and its ToDo are closed.
"""

import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import frappe
from frappe import _
from frappe.utils import escape_html, flt, get_datetime, get_time, get_url_to_form, now_datetime, today

from periskope_connector.system import insert_as_system
from periskope_connector.text_rules import is_acknowledgement

LOOKBACK_DAYS = 7
MAX_SPAN_DAYS = 60

WEEKDAYS = {
	"Monday to Saturday": {0, 1, 2, 3, 4, 5},
	"Monday to Friday": {0, 1, 2, 3, 4},
	"Every day": {0, 1, 2, 3, 4, 5, 6},
}


def get_config(settings=None) -> SimpleNamespace:
	settings = settings or frappe.get_single("Periskope Settings")
	emails = [e.strip() for e in re.split(r"[,\n;]", settings.alert_emails or "") if e.strip()]
	return SimpleNamespace(
		enabled=bool(settings.enable_reply_reminders),
		start=get_time(settings.working_hours_start or "09:00:00"),
		end=get_time(settings.working_hours_end or "19:00:00"),
		days=WEEKDAYS.get(settings.working_days or "", WEEKDAYS["Monday to Saturday"]),
		remind_after=flt(settings.remind_after_hours) or 1.0,
		escalate_after=flt(settings.escalate_after_hours) or 2.0,
		emails=emails,
		groups=bool(settings.remind_group_chats),
	)


# --- Working hours -------------------------------------------------------------------------------


def is_working_time(moment: datetime, cfg) -> bool:
	return moment.weekday() in cfg.days and cfg.start <= moment.time() < cfg.end


def working_seconds(start: datetime, end: datetime, cfg) -> float:
	"""Seconds between two site-time datetimes that fall inside working hours."""
	if end <= start:
		return 0.0
	total = 0.0
	day = start.date()
	for _i in range(MAX_SPAN_DAYS):
		if day > end.date():
			break
		if day.weekday() in cfg.days:
			window_start = max(datetime.combine(day, cfg.start), start)
			window_end = min(datetime.combine(day, cfg.end), end)
			if window_end > window_start:
				total += (window_end - window_start).total_seconds()
		day += timedelta(days=1)
	return total


# --- Which customer messages need a reply ----------------------------------------------------------


def needs_reply(message) -> bool:
	"""False for acknowledgements, emoji-only and 1-2 character texts. A short real question
	such as "Price?" still needs a reply. If the AI already tagged the message and decided no
	reply is needed, that is respected."""
	body = (message.body or "").strip()
	if not body:
		return bool(message.message_type and message.message_type != "chat")  # an attachment
	if is_acknowledgement(body):
		return False
	if message.classification_status == "Done" and not message.needs_reply:
		return False
	return True


def unanswered(messages: list) -> list:
	"""Customer messages, oldest first, sent after the rep's last message and needing a reply."""
	last_reply = max(
		(m.message_timestamp for m in messages if m.direction == "Outgoing" and m.message_timestamp),
		default=None,
	)
	return [
		m
		for m in messages
		if m.direction == "Incoming"
		and m.message_timestamp
		and (last_reply is None or m.message_timestamp > last_reply)
		and needs_reply(m)
	]


# --- Reminders ----------------------------------------------------------------------------------


def _open_reminder(chat_id: str):
	name = frappe.db.get_value(
		"Periskope Reply Reminder", {"chat_id": chat_id, "status": ["in", ["Open", "Escalated"]]}
	)
	return frappe.get_doc("Periskope Reply Reminder", name) if name else None


def _todo_text(last, first_waiting, hours) -> str:
	who = escape_html(last.contact_number or _("a WhatsApp group"))
	return (
		f"<p>{_('A customer is waiting for a WhatsApp reply')}: <b>{who}</b></p>"
		f"<p>{_('Waiting since')} {first_waiting.strftime('%d %b %Y %H:%M')} "
		f"({hours:.1f} {_('working hours')})</p>"
		f"<p>{_('Latest message')}: {escape_html((last.body or _('(attachment)'))[:300])}</p>"
	)


def create_reminder(chat_id: str, pending: list, rep: str | None, cfg, now: datetime, hours: float):
	first, last = pending[0], pending[-1]
	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": rep,
			"reference_type": "Periskope Message",
			"reference_name": last.name,
			"description": _todo_text(last, first.message_timestamp, hours),
			"priority": "Medium",
			"date": today(),
			"status": "Open",
		}
	).insert()
	return insert_as_system(
		{
			"doctype": "Periskope Reply Reminder",
			"chat_id": chat_id,
			"status": "Open",
			"rep": rep,
			"contact_number": last.contact_number,
			"is_group": last.is_group,
			"waiting_since": first.message_timestamp,
			"reminded_on": now,
			"last_message": last.name,
			"message_preview": (last.body or "")[:300],
			"todo": todo.name,
		}
	)


def escalate(reminder, cfg, now: datetime, hours: float) -> bool:
	"""Email the managers. Returns True once the email is queued."""
	if not cfg.emails:
		return False
	who = reminder.contact_number or _("a WhatsApp group")
	rep = reminder.rep or _("no rep assigned")
	try:
		frappe.sendmail(
			recipients=cfg.emails,
			subject=_("Customer waiting for a WhatsApp reply: {0}").format(who),
			message=(
				f"<p>{_('A customer has been waiting for a reply for')} <b>{hours:.1f} "
				f"{_('working hours')}</b>.</p>"
				f"<p>{_('Customer')}: {escape_html(who)}<br>{_('Sales rep')}: {escape_html(rep)}<br>"
				f"{_('Waiting since')}: {reminder.waiting_since}</p>"
				f"<p>{_('Latest message')}: {escape_html(reminder.message_preview or '')}</p>"
				f'<p><a href="{get_url_to_form("Periskope Reply Reminder", reminder.name)}">'
				f"{_('Open the reminder')}</a></p>"
			),
			reference_doctype="Periskope Reply Reminder",
			reference_name=reminder.name,
		)
	except Exception:
		frappe.log_error(title="Periskope: could not email the reply escalation")
		return False
	reminder.db_set({"status": "Escalated", "escalated_on": now})
	return True


def close(reminder, now: datetime):
	reminder.db_set({"status": "Closed", "closed_on": now})
	if reminder.todo and frappe.db.get_value("ToDo", reminder.todo, "status") == "Open":
		frappe.db.set_value("ToDo", reminder.todo, "status", "Closed")


def _check_chat(chat_id: str, messages: list, cfg, now: datetime, stats: dict):
	"""Create, escalate or close the reminder of one chat."""
	pending = unanswered(messages)
	reminder = _open_reminder(chat_id)

	if not pending:
		if reminder:
			close(reminder, now)
			stats["closed"] += 1
		return

	if not reminder:
		waited = working_seconds(pending[0].message_timestamp, now, cfg) / 3600
		if waited >= cfg.remind_after:
			rep = next((m.rep for m in reversed(messages) if m.rep), None)
			create_reminder(chat_id, pending, rep, cfg, now, waited)
			stats["todos"] += 1
	elif reminder.status == "Open":
		since_todo = working_seconds(get_datetime(reminder.reminded_on), now, cfg) / 3600
		if since_todo >= cfg.escalate_after:
			waited = working_seconds(get_datetime(reminder.waiting_since), now, cfg) / 3600
			if escalate(reminder, cfg, now, waited):
				stats["escalated"] += 1


def _run(now: datetime | None) -> dict:
	cfg = get_config()
	stats = {"todos": 0, "escalated": 0, "closed": 0, "errors": 0}
	if not cfg.enabled:
		return {**stats, "skipped": "reminders are off"}
	now = now or now_datetime()
	if not is_working_time(now, cfg):
		return {**stats, "skipped": "outside working hours"}

	rows = frappe.get_all(
		"Periskope Message",
		filters={"message_timestamp": [">=", now - timedelta(days=LOOKBACK_DAYS)]},
		fields=[
			"name",
			"chat_id",
			"direction",
			"body",
			"message_type",
			"message_timestamp",
			"rep",
			"contact_number",
			"is_group",
			"classification_status",
			"needs_reply",
		],
		order_by="message_timestamp asc",
	)
	chats: dict[str, list] = {}
	for row in rows:
		if row.chat_id:
			chats.setdefault(row.chat_id, []).append(row)

	for chat_id, messages in chats.items():
		if messages[0].is_group and not cfg.groups:
			continue
		# One chat failing (for example the ToDo cannot be saved) must not stop the other chats,
		# and must not leave half of its own work behind.
		frappe.db.savepoint("periskope_reminder_chat")
		try:
			_check_chat(chat_id, messages, cfg, now, stats)
		except Exception:
			frappe.db.rollback(save_point="periskope_reminder_chat")
			frappe.log_error(title=f"Periskope: reply reminder failed for chat {chat_id}")
			stats["errors"] += 1
	return stats


def run(now: datetime | None = None) -> dict:
	"""Scheduler entry point (every 10 minutes). Returns counts, handy for testing.

	A failure is written to the Error Log with its traceback and then raised again, so it also
	shows as failed in the Scheduled Job Log (and to the user on "Check Reply Reminders Now").
	"""
	try:
		return _run(now)
	except Exception:
		frappe.log_error(title="Periskope: reply reminder check failed")
		raise
