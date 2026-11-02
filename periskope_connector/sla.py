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
	from periskope_connector.access import is_manager

	managers = [
		u
		for u in frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name")
		if is_manager(u) and u != "Administrator"
	]
	for row in rows:
		who = row.chat_name or (f"+{row.contact_number}" if row.contact_number else row.name)
		subject = _("WhatsApp reply overdue: {0}").format(who)
		assignees = frappe.parse_json(row._assign or "[]") or []
		_bell(sorted(set(assignees + managers)), row.name, subject)
		if cfg.emails:
			safety.sendmail(
				recipients=cfg.emails,
				subject=subject,
				message=_("{0} has been waiting for a reply since before {1}. Open the chat: {2}").format(
					who, row.response_due, frappe.utils.get_url(f"/whatsapp/inbox/{row.name}")
				),
				delayed=True,
			)
		frappe.db.set_value(
			"Periskope Chat", row.name, {"breach_alerted": 1, "response_breached": 1}, update_modified=False
		)
		workflow.log(
			row.name,
			"SLA Breached",
			_("Reply overdue since {0}").format(row.response_due),
			user="Administrator",
		)
	frappe.db.commit()
	return len(rows)


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
