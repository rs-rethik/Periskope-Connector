"""SLA policies for the WhatsApp inbox: how soon a customer gets an answer, and how soon an opened
chat is resolved.

A chat's clocks (workflow.py sets them):
- reply: from a customer's first unanswered message (waiting_since) to response_due. Our reply stops
  it; an AI holding reply does not count as an answer.
- resolution: from when the chat opened (opened_on) to resolution_due. Resolving or closing stops it.
A Pending chat (waiting on the customer) is not counted late. With "Count Working Hours Only" the
clocks run in the working hours, days and holidays of Periskope Settings (reminders.py).

What the list shows (state()) is worked out when it is read, so it is right without any job. A job
every 5 minutes (check_breaches) alerts the assignee and the managers once per late reply.
"""

from datetime import datetime, timedelta

import frappe
from frappe import _
from frappe.utils import get_datetime, now_datetime

from periskope_connector import reminders, safety

DUE_SOON = 0.2  # the last fifth of the time is "due soon"
FIELDS = ["name", "status", "waiting_since", "response_due", "opened_on", "resolution_due", "resolved_on"]


def policy_for(number: str | None, team: str | None) -> frappe._dict | None:
	"""The enabled policy for a chat: the highest priority one naming its number or team, else the
	default one."""
	policies = frappe.get_all(
		"Periskope SLA Policy",
		filters={"enabled": 1},
		fields=[
			"name",
			"is_default",
			"priority",
			"first_response_minutes",
			"resolution_minutes",
			"use_working_hours",
		],
		order_by="priority desc, modified desc",
	)
	if not policies:
		return None
	names = [p.name for p in policies]
	by_number = set(
		frappe.get_all(
			"Periskope Agent Number",
			filters={
				"parenttype": "Periskope SLA Policy",
				"parent": ["in", names],
				"whatsapp_number": number or "",
			},
			pluck="parent",
		)
	)
	by_team = set(
		frappe.get_all(
			"Periskope SLA Team",
			filters={"parenttype": "Periskope SLA Policy", "parent": ["in", names], "team": team or ""},
			pluck="parent",
		)
	)
	for p in policies:
		if p.name in by_number or p.name in by_team:
			return p
	return next((p for p in policies if p.is_default), None)


def add_minutes(start: datetime, minutes: int, working_hours: bool) -> datetime:
	"""The moment `minutes` after `start`, counting only working time when asked."""
	start = get_datetime(start)
	if not working_hours:
		return start + timedelta(minutes=minutes)
	cfg = reminders.get_config()
	left = timedelta(minutes=minutes)
	moment = start
	for _day in range(370):  # a year of days at most
		day_start = datetime.combine(moment.date(), cfg.start)
		day_end = datetime.combine(moment.date(), cfg.end)
		working_day = moment.weekday() in cfg.days and moment.date() not in cfg.holidays
		if working_day and moment < day_end:
			begin = max(moment, day_start)
			if begin + left <= day_end:
				return begin + left
			left -= day_end - begin
		moment = datetime.combine(moment.date() + timedelta(days=1), cfg.start)
	return start + timedelta(minutes=minutes)


def due(policy, field: str, start) -> datetime | None:
	minutes = (policy or {}).get(field) or 0
	if not policy or minutes <= 0 or not start:
		return None
	return add_minutes(start, minutes, bool(policy.use_working_hours))


def state(chat, now: datetime | None = None) -> dict:
	"""{"sla": On Track / Due Soon / Breached / Met / "", "due": the next deadline} for a chat row."""
	now = now or now_datetime()
	if chat.status in ("Resolved", "Closed"):
		met = (
			chat.resolution_due
			and chat.resolved_on
			and get_datetime(chat.resolved_on) <= get_datetime(chat.resolution_due)
		)
		return {"sla": "Met" if met else "", "due": None}
	if chat.status == "Pending":
		return {"sla": "", "due": None}
	deadlines = []
	if chat.waiting_since and chat.response_due:
		deadlines.append((get_datetime(chat.waiting_since), get_datetime(chat.response_due)))
	if chat.opened_on and chat.resolution_due:
		deadlines.append((get_datetime(chat.opened_on), get_datetime(chat.resolution_due)))
	if not deadlines:
		return {"sla": "", "due": None}
	start, deadline = min(deadlines, key=lambda d: d[1])
	if now > deadline:
		return {"sla": "Breached", "due": deadline}
	span = (deadline - start).total_seconds() or 1
	if (deadline - now).total_seconds() / span <= DUE_SOON:
		return {"sla": "Due Soon", "due": deadline}
	return {"sla": "On Track", "due": deadline}


def breached_condition(table: str = "c") -> str:
	"""SQL for chats whose reply or resolution is late now (the "SLA breached" filter). The query's
	values must carry sla_now (site time: the database's own clock may be in another zone)."""
	return (
		f"({table}.status = 'Open' and ("
		f"({table}.waiting_since is not null and {table}.response_due < %(sla_now)s)"
		f" or ({table}.resolution_due is not null and {table}.resolution_due < %(sla_now)s)))"
	)


def check_breaches() -> int:
	"""Scheduler, every 5 minutes: a customer waiting past the reply deadline is an SLA breach. The
	assignee and the managers hear of it once (a bell, and the managers an email through the
	reminders' alert settings); the chat's activity records it."""
	rows = frappe.db.sql(
		f"""select c.name, c.chat_name, c.contact_number, c._assign, c.whatsapp_number, c.response_due
		from `tabPeriskope Chat` c where {breached_condition()} and c.breach_alerted = 0
		and c.waiting_since is not null and c.response_due < %(sla_now)s limit 50""",
		{"sla_now": now_datetime()},
		as_dict=True,
	)
	cfg = reminders.get_config()
	from periskope_connector import workflow
	from periskope_connector.access import managers_by_number, managers_of

	managers = managers_by_number()
	for row in rows:
		who = row.chat_name or (f"+{row.contact_number}" if row.contact_number else row.name)
		subject = _("WhatsApp reply overdue: {0}").format(who)
		assignees = frappe.parse_json(row._assign or "[]") or []
		# Marked first, then the bell, then the email: a site that cannot send email (no outgoing
		# account) still rings the bell, once, and the next run does not fail on the same chat again.
		frappe.db.set_value(
			"Periskope Chat", row.name, {"breach_alerted": 1, "response_breached": 1}, update_modified=False
		)
		_bell(sorted(set(assignees + managers_of(row.whatsapp_number, managers))), row.name, subject)
		if cfg.emails:
			_email(cfg.emails, row, who, subject)
		workflow.log(
			row.name,
			"SLA Breached",
			_("Reply overdue since {0}").format(row.response_due),
			user="Administrator",
		)
	late = _resolution_breaches(cfg, managers)
	frappe.db.commit()
	return len(rows) + late


def _resolution_breaches(cfg, managers: dict) -> int:
	"""Open chats past their resolution deadline: the assignee and the managers hear once (the same
	way as a late reply); reopening the chat starts a new deadline and a new alert."""
	c = frappe.qb.DocType("Periskope Chat")
	rows = (
		frappe.qb.from_(c)
		.select(c.name, c.chat_name, c.contact_number, c._assign, c.resolution_due, c.whatsapp_number)
		.where(
			c.status.isin(["Open", "Pending"])
			& c.resolution_due.isnotnull()
			& (c.resolution_due < now_datetime())
			& c.resolved_on.isnull()
			& (c.resolution_alerted == 0)
		)
		.limit(50)
	).run(as_dict=True)
	from periskope_connector import workflow
	from periskope_connector.access import managers_of

	for row in rows:
		who = row.chat_name or (f"+{row.contact_number}" if row.contact_number else row.name)
		subject = _("WhatsApp chat not resolved in time: {0}").format(who)
		frappe.db.set_value("Periskope Chat", row.name, "resolution_alerted", 1, update_modified=False)
		assignees = frappe.parse_json(row._assign or "[]") or []
		_bell(sorted(set(assignees + managers_of(row.whatsapp_number, managers))), row.name, subject)
		if cfg.emails:
			_email(
				cfg.emails,
				frappe._dict(row, response_due=row.resolution_due),
				who,
				subject,
				_("{0}'s chat was due to be resolved by {1} and is still open. Open the chat: {2}"),
			)
		workflow.log(
			row.name,
			"SLA Breached",
			_("Not resolved by {0}").format(row.resolution_due),
			user="Administrator",
		)
	return len(rows)


def _email(recipients: list[str], row, who: str, subject: str, text: str | None = None):
	"""The managers' email about a late reply (or a chat not resolved in time: `text`). Never raises:
	its own writes are undone and the failure logged, the breach stays recorded."""
	frappe.db.savepoint("periskope_sla_email")
	try:
		safety.sendmail(
			recipients=recipients,
			subject=subject,
			message=(
				text or _("{0} has been waiting for a reply since before {1}. Open the chat: {2}")
			).format(who, row.response_due, frappe.utils.get_url(f"/whatsapp/inbox/{row.name}")),
			delayed=True,
		)
	except Exception:
		frappe.db.rollback(save_point="periskope_sla_email")
		frappe.log_error(title=f"Periskope: could not email the SLA breach of chat {row.name}")


def _bell(users: list[str], chat: str, subject: str):
	if not users:
		return
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	enqueue_create_notification(
		users,
		{
			"type": "Alert",
			"document_type": "Periskope Chat",
			"document_name": chat,
			"subject": subject,
			"from_user": "Administrator",
		},
	)
