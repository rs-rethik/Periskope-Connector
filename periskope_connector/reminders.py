"""The conversation SLA engine: remind and escalate when a customer is waiting for a reply.

Runs every 10 minutes. Per chat (not per message), with a tiered clock in working hours:
1. A customer message that needs a reply and has no later rep message starts the clock.
2. Only working hours count (site time, e.g. 09:00-19:00, Monday-Saturday, minus a Holiday
   List), so a message sent at night starts counting the next morning and nothing fires
   outside working hours.
3. The SLA levels (Periskope Settings > Reply Reminders) say what happens and when: each row has
   the working minutes to wait, the lowest severity it applies to, and an action: notify the rep
   (bell), a ToDo for the rep, an email to the managers, to the escalation list (the MD), or to
   the recipients on the row. The starting rows are: notify at 1 working hour, ToDo plus managers
   at 3, escalation list at 8, and for High severity all four after 30 minutes.
4. An action runs once per waiting period (the same action to the same recipients is not repeated
   by a later row).
5. When the rep replies, the reminder and its ToDo are closed and the response time recorded.
   A new incoming message after the reply starts a fresh cycle.

Group chats: any rep's message in the group counts as the reply (when group chats are included).
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

NOTIFY_REP = "Notify Rep"
TODO_FOR_REP = "ToDo for Rep"
EMAIL_MANAGERS = "Email Managers"
EMAIL_ESCALATION = "Email Escalation List"
EMAIL_RECIPIENTS = "Email Recipients"

# The "level" shown on a reminder: 1 rep notified, 2 ToDo or managers emailed, 3 escalation emailed.
ACTION_LEVEL = {
	NOTIFY_REP: 1,
	TODO_FOR_REP: 2,
	EMAIL_MANAGERS: 2,
	EMAIL_RECIPIENTS: 2,
	EMAIL_ESCALATION: 3,
}
SEVERITY_RANK = {"Low": 0, "Medium": 1, "High": 2}


def default_levels() -> list[SimpleNamespace]:
	"""The starting SLA levels: a bell at 1 working hour, a ToDo and the managers at 3, the
	escalation list at 8, and all four after 30 minutes for a High severity message."""
	rows = [
		(60, "Low", NOTIFY_REP),
		(180, "Low", TODO_FOR_REP),
		(180, "Low", EMAIL_MANAGERS),
		(480, "Low", EMAIL_ESCALATION),
		(30, "High", NOTIFY_REP),
		(30, "High", TODO_FOR_REP),
		(30, "High", EMAIL_MANAGERS),
		(30, "High", EMAIL_ESCALATION),
	]
	return [
		SimpleNamespace(wait_minutes=minutes, min_severity=severity, action=action, recipients="")
		for minutes, severity, action in rows
	]


WEEKDAYS = {
	"Monday to Saturday": {0, 1, 2, 3, 4, 5},
	"Monday to Friday": {0, 1, 2, 3, 4},
	"Every day": {0, 1, 2, 3, 4, 5, 6},
}


def get_config(settings=None) -> SimpleNamespace:
	settings = settings or frappe.get_single("Periskope Settings")

	def emails_of(value) -> list[str]:
		return [e.strip() for e in re.split(r"[,\n;]", value or "") if e.strip()]

	emails = emails_of(settings.alert_emails)
	levels = [
		SimpleNamespace(
			wait_minutes=flt(row.wait_minutes),
			min_severity=row.min_severity or "Low",
			action=row.action,
			recipients=row.recipients or "",
		)
		for row in sorted(settings.get("sla_levels") or [], key=lambda r: (flt(r.wait_minutes), r.idx))
		if row.action
	]
	extra_words = [w.strip().lower() for w in re.split(r"[,\n;]", settings.ack_words or "") if w.strip()]
	return SimpleNamespace(
		enabled=bool(settings.enable_reply_reminders),
		start=get_time(settings.working_hours_start or "09:00:00"),
		end=get_time(settings.working_hours_end or "19:00:00"),
		days=WEEKDAYS.get(settings.working_days or "", WEEKDAYS["Monday to Saturday"]),
		holidays=_holiday_dates(settings.holiday_list),
		levels=levels,
		emails=emails,
		md_emails=emails_of(settings.escalation_emails) or emails,
		groups=bool(settings.remind_group_chats),
		ack_words=extra_words,
	)


def _holiday_dates(list_name: str | None) -> set:
	"""The dates of a Holiday List (e.g. the Diwali shutdown), if one is linked."""
	if not list_name or not frappe.db.exists("DocType", "Holiday List"):
		return set()
	try:
		rows = frappe.get_all(
			"Holiday",
			filters={"parent": list_name, "parenttype": "Holiday List"},
			pluck="holiday_date",
			limit=370,
		)
		return {r if isinstance(r, datetime) else datetime.combine(r, datetime.min.time()) for r in rows}
	except Exception:
		return set()


# --- Working hours -------------------------------------------------------------------------------


def is_working_time(moment: datetime, cfg) -> bool:
	return (
		moment.weekday() in cfg.days
		and moment.date() not in cfg.holidays
		and cfg.start <= moment.time() < cfg.end
	)


def working_seconds(start: datetime, end: datetime, cfg) -> float:
	"""Seconds between two site-time datetimes that fall inside working hours."""
	if end <= start:
		return 0.0
	total = 0.0
	day = start.date()
	for _i in range(MAX_SPAN_DAYS):
		if day > end.date():
			break
		if day.weekday() in cfg.days and day not in cfg.holidays:
			window_start = max(datetime.combine(day, cfg.start), start)
			window_end = min(datetime.combine(day, cfg.end), end)
			if window_end > window_start:
				total += (window_end - window_start).total_seconds()
		day += timedelta(days=1)
	return total


# --- Which customer messages need a reply ----------------------------------------------------------


def needs_reply(message, extra_words: list[str] | None = None) -> bool:
	"""False for acknowledgements, emoji-only and 1-2 character texts. A short real question
	such as "Price?" still needs a reply. If the AI already tagged the message and decided no
	reply is needed, that is respected."""
	body = (message.body or "").strip()
	if not body:
		return bool(message.message_type and message.message_type != "text")  # an attachment
	if is_acknowledgement(body, extra_words):
		return False
	if message.classification_status == "Done" and not message.needs_reply:
		return False
	return True


def unanswered(messages: list, extra_words: list[str] | None = None) -> list:
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
		and needs_reply(m, extra_words)
	]


# --- Notifications, ToDos, emails -------------------------------------------------------------------


def _open_reminder(chat_id: str):
	name = frappe.db.get_value(
		"Periskope Reply Reminder", {"chat_id": chat_id, "status": ["in", ["Open", "Escalated"]]}
	)
	return frappe.get_doc("Periskope Reply Reminder", name) if name else None


def _key(row) -> str:
	"""What makes two SLA rows the same step: the action and who it goes to."""
	recipients = ",".join(
		sorted(e.strip().lower() for e in re.split(r"[,\n;]", row.recipients or "") if e.strip())
	)
	return f"{row.action}|{recipients}"


def _done(reminder) -> set[str]:
	"""The steps already taken in this waiting period."""
	return {key for key in (reminder.fired_rows or "").split("\n") if key}


def _bell(rep: str | None, last, hours: float):
	"""L1: a notification for the rep, no email yet."""
	if not rep or not frappe.db.get_value("User", rep, "enabled"):
		return
	frappe.get_doc(
		{
			"doctype": "Notification Log",
			"type": "Alert",
			"for_user": rep,
			"subject": _("Customer waiting for a WhatsApp reply: {0}").format(
				last.contact_number or _("a WhatsApp group")
			)[:140],
			"document_type": "Periskope Message",
			"document_name": last.name,
		}
	).insert(ignore_permissions=True)


def _todo_text(last, first_waiting, hours) -> str:
	who = escape_html(last.contact_number or _("a WhatsApp group"))
	return (
		f"<p>{_('A customer is waiting for a WhatsApp reply')}: <b>{who}</b></p>"
		f"<p>{_('Waiting since')} {first_waiting.strftime('%d %b %Y %H:%M')} "
		f"({hours:.1f} {_('working hours')})</p>"
		f"<p>{_('Latest message')}: {escape_html((last.body or _('(attachment)'))[:300])}</p>"
	)


def _send_email(subject: str, message: str, recipients: list[str], reminder) -> bool:
	if not recipients:
		return False
	try:
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			reference_doctype="Periskope Reply Reminder",
			reference_name=reminder.name,
		)
	except Exception:
		frappe.log_error(title="Periskope: could not email the reply escalation")
		return False
	return True


def _escalation_email(reminder, recipients: list[str], hours: float, to_md: bool) -> bool:
	who = reminder.contact_number or _("a WhatsApp group")
	rep = reminder.rep or _("no rep assigned")
	subject = _("Customer waiting for a WhatsApp reply: {0}").format(who)
	if to_md:
		subject = _("[MD] {0}").format(subject)
	ok = _send_email(
		subject,
		(
			f"<p>{_('A customer has been waiting for a reply for')} <b>{hours:.1f} "
			f"{_('working hours')}</b>.</p>"
			f"<p>{_('Customer')}: {escape_html(who)}<br>{_('Sales rep')}: {escape_html(rep)}<br>"
			f"{_('Waiting since')}: {reminder.waiting_since}</p>"
			f"<p>{_('Latest message')}: {escape_html(reminder.message_preview or '')}</p>"
			f'<p><a href="{get_url_to_form("Periskope Reply Reminder", reminder.name)}">'
			f"{_('Open the reminder')}</a></p>"
		),
		recipients,
		reminder,
	)
	if ok and not to_md:
		# The chat's messages count as escalated from the moment the managers were told.
		frappe.db.sql(
			"""update `tabPeriskope Message` set escalated = 1
			where chat_id = %s and direction = 'Incoming' and replied = 0 and message_timestamp >= %s""",
			(reminder.chat_id, reminder.waiting_since),
		)
	return ok


def create_reminder(chat_id: str, pending: list, rep: str | None, cfg, now: datetime, hours: float):
	"""Open the reminder. The actions of the SLA levels run afterwards, see _check_chat."""
	first, last = pending[0], pending[-1]
	return insert_as_system(
		{
			"doctype": "Periskope Reply Reminder",
			"chat_id": chat_id,
			"status": "Open",
			"level": 0,
			"rep": rep,
			"contact_number": last.contact_number,
			"is_group": last.is_group,
			"waiting_since": first.message_timestamp,
			"reminded_on": now,
			"last_message": last.name,
			"message_preview": (last.body or "")[:300],
		}
	)


def _create_todo(reminder, hours: float) -> bool:
	if reminder.todo:
		return True
	todo = frappe.get_doc(
		{
			"doctype": "ToDo",
			"allocated_to": reminder.rep,
			"reference_type": "Periskope Message",
			"reference_name": reminder.last_message,
			"description": _todo_text(
				frappe._dict(contact_number=reminder.contact_number, body=reminder.message_preview),
				get_datetime(reminder.waiting_since),
				hours,
			),
			"priority": "Medium",
			"date": today(),
			"status": "Open",
		}
	).insert()
	reminder.db_set("todo", todo.name)
	return True


def perform(row, reminder, last, cfg, now: datetime, hours: float) -> bool:
	"""Carry out one SLA row. Returns False when it should be tried again on the next run (an email
	that could not be sent)."""
	action = row.action
	if action == NOTIFY_REP:
		_bell(reminder.rep, last, hours)
		return True
	if action == TODO_FOR_REP:
		return _create_todo(reminder, hours)
	if action == EMAIL_MANAGERS:
		recipients, to_md = cfg.emails, False
	elif action == EMAIL_ESCALATION:
		recipients, to_md = cfg.md_emails, True
	elif action == EMAIL_RECIPIENTS:
		recipients, to_md = [e.strip() for e in re.split(r"[,\n;]", row.recipients or "") if e.strip()], False
	else:
		return True
	if not recipients:
		return True  # nobody to tell: not an error, and not worth retrying
	return _escalation_email(reminder, recipients, hours, to_md=to_md)


def close(reminder, now: datetime, messages: list | None = None, cfg=None):
	"""The rep replied (or any rep, in a group): close the reminder, its ToDo, and record how
	long the first answer took in working hours."""
	values = {"status": "Closed", "closed_on": now}
	if messages and cfg:
		reply = max(
			(m.message_timestamp for m in messages if m.direction == "Outgoing" and m.message_timestamp),
			default=None,
		)
		if reply:
			values["first_response_secs"] = int(
				working_seconds(get_datetime(reminder.waiting_since), reply, cfg)
			)
	reminder.db_set(values)
	if reminder.todo and frappe.db.get_value("ToDo", reminder.todo, "status") == "Open":
		frappe.db.set_value("ToDo", reminder.todo, "status", "Closed")


# --- The engine --------------------------------------------------------------------------------------


def _check_chat(chat_id: str, messages: list, cfg, now: datetime, stats: dict):
	"""Advance one chat through the SLA levels."""
	pending = unanswered(messages, cfg.ack_words)
	reminder = _open_reminder(chat_id)

	if not pending:
		if reminder:
			close(reminder, now, messages, cfg)
			stats["closed"] += 1
		return

	hours = working_seconds(pending[0].message_timestamp, now, cfg) / 3600
	severity = max((SEVERITY_RANK.get(getattr(m, "severity", None) or "Low", 0) for m in pending), default=0)
	done = _done(reminder) if reminder else set()
	due = [
		row
		for row in cfg.levels
		if hours * 60 >= row.wait_minutes
		and severity >= SEVERITY_RANK.get(row.min_severity, 0)
		and _key(row) not in done
	]
	if not due:
		return

	if not reminder:
		rep = next((m.rep for m in reversed(messages) if m.rep), None)
		reminder = create_reminder(chat_id, pending, rep, cfg, now, hours)
		stats["notified"] += 1

	escalated = False
	for row in due:
		if _key(row) in done:
			continue  # an earlier row in this pass did the same
		if not perform(row, reminder, pending[-1], cfg, now, hours):
			continue
		done.add(_key(row))
		if row.action == EMAIL_ESCALATION:
			stats["md_emailed"] += 1
		elif row.action != NOTIFY_REP:
			escalated = True
	values = {
		"fired_rows": "\n".join(sorted(done)),
		"level": max((ACTION_LEVEL.get(key.split("|")[0], 0) for key in done), default=0),
	}
	if escalated and reminder.status != "Escalated":
		values.update({"status": "Escalated", "escalated_on": now})
		stats["escalated"] += 1
	reminder.db_set(values)


def _run(now: datetime | None) -> dict:
	cfg = get_config()
	stats = {"notified": 0, "todos": 0, "escalated": 0, "md_emailed": 0, "closed": 0, "errors": 0}
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
			"severity",
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
	stats["todos"] = stats["escalated"]  # the ToDo now belongs to L2; keep the old key meaningful
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
