"""The Today page: what the signed-in rep should do now, on one screen.

- Waiting: chats of theirs (or nobody's) where the customer wrote last, the longest wait first.
- Tasks: their open CRM tasks due today or overdue.
- Quiet: their open leads and deals with no change and no WhatsApp message for QUIET_DAYS.
- New: numbers that wrote for the first time today, and whether the AI welcomed them.
Chats follow the inbox's access rules (api.inbox._where)."""

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, get_fullname, getdate, now_datetime, today

from periskope_connector import access, reminders
from periskope_connector.api.inbox import CHAT_COLUMNS, _decorate, _where

LIMIT = 8
QUIET_DAYS = 7
OPEN_TASK = ("Backlog", "Todo", "In Progress")
CLOSED_TYPES = ("Won", "Lost", "On Hold")


@frappe.whitelist()
def get_today() -> dict:
	access.require_inbox_user()
	user = frappe.session.user
	now = now_datetime()
	waiting, waiting_total, breached = _waiting()
	tasks, tasks_total, overdue = _tasks(user)
	quiet, quiet_total = _quiet(user, now)
	new, new_total = _new_today()
	return {
		"user": {"first_name": (get_fullname(user) or user).split(" ")[0], "is_manager": access.is_manager()},
		"now": str(now),
		"office": _office(now),
		"waiting": {"rows": waiting, "total": waiting_total, "breached": breached},
		"tasks": {"rows": tasks, "total": tasks_total, "overdue": overdue},
		"quiet": {"rows": quiet, "total": quiet_total, "days": QUIET_DAYS},
		"new": {"rows": new, "total": new_total},
	}


def _office(now) -> dict:
	try:
		cfg = reminders.get_config()
	except Exception:
		return {}
	return {
		"open": reminders.is_working_time(now, cfg),
		"start": cfg.start.strftime("%H:%M"),
		"end": cfg.end.strftime("%H:%M"),
	}


def _waiting() -> tuple[list, int, int]:
	conditions, values = _where({"status": "active", "unanswered": 1})
	conditions += [
		"c.is_group = 0",
		"c.chat_id not like '%%@g.us'",
		"(c._assign like %(assign)s or ifnull(c._assign, '') in ('', '[]'))",
	]
	where = " and ".join(conditions)
	rows = frappe.db.sql(
		f"""select {CHAT_COLUMNS} from `tabPeriskope Chat` c where {where}
		order by coalesce(c.waiting_since, c.last_incoming_at) asc limit {LIMIT}""",
		values,
		as_dict=True,
	)
	total = frappe.db.sql(f"select count(*) from `tabPeriskope Chat` c where {where}", values)[0][0]
	from periskope_connector.sla import breached_condition

	values["sla_now"] = now_datetime()
	breached = frappe.db.sql(
		f"select count(*) from `tabPeriskope Chat` c where {where} and {breached_condition('c')}", values
	)[0][0]
	rows = _decorate(rows)
	for row in rows:
		row.waiting_since = row.waiting_since or row.last_incoming_at
	return rows, total, breached


def _tasks(user: str) -> tuple[list, int, int]:
	if not frappe.db.exists("DocType", "CRM Task"):
		return [], 0, 0
	end_of_today = f"{today()} 23:59:59"
	filters = {"assigned_to": user, "status": ["in", OPEN_TASK], "due_date": ["<=", end_of_today]}
	rows = frappe.get_all(
		"CRM Task",
		filters=filters,
		fields=["name", "title", "status", "priority", "due_date", "reference_doctype", "reference_docname"],
		order_by="due_date asc",
		limit=LIMIT,
	)
	total = frappe.db.count("CRM Task", filters)
	overdue = frappe.db.count("CRM Task", {**filters, "due_date": ["<", f"{today()} 00:00:00"]})
	titles = {}
	for doctype, field in (("CRM Lead", "lead_name"), ("CRM Deal", "organization")):
		names = [r.reference_docname for r in rows if r.reference_doctype == doctype]
		if names:
			titles.update(
				dict(frappe.get_all(doctype, {"name": ["in", names]}, ["name", field], as_list=True))
			)
	for row in rows:
		row.reference_title = titles.get(row.reference_docname) or row.reference_docname
		row.overdue = bool(row.due_date and getdate(row.due_date) < getdate(today()))
	return rows, total, overdue


def _quiet(user: str, now) -> tuple[list, int]:
	cutoff = add_days(now, -QUIET_DAYS)
	parts, values = [], {"user": user, "cutoff": cutoff}
	for doctype, owner, link, title, extra in (
		(
			"CRM Lead",
			"lead_owner",
			"crm_lead",
			"coalesce(nullif(r.lead_name, ''), r.organization)",
			"r.converted = 0",
		),
		("CRM Deal", "deal_owner", "crm_deal", "coalesce(nullif(r.organization, ''), r.lead_name)", "1=1"),
	):
		status_doctype = f"{doctype} Status"
		closed = frappe.get_all(status_doctype, {"type": ["in", CLOSED_TYPES]}, pluck="name") or [""]
		key = link + "_closed"
		values[key] = tuple(closed)
		parts.append(
			f"""select '{doctype}' as doctype, r.name, {title} as title, r.status, r.modified
			from `tab{doctype}` r where r.{owner} = %(user)s and {extra} and r.status not in %({key})s
			and r.modified < %(cutoff)s and not exists (select 1 from `tabPeriskope Message` m
			where m.{link} = r.name and m.creation >= %(cutoff)s)"""
		)
	union = " union all ".join(parts)
	rows = frappe.db.sql(
		f"select * from ({union}) q order by modified asc limit {LIMIT}", values, as_dict=True
	)
	total = frappe.db.sql(f"select count(*) from ({union}) q", values)[0][0]
	for row in rows:
		row.idle_days = (now - get_datetime(row.modified)).days
		row.route = f"/crm/{'leads' if row.doctype == 'CRM Lead' else 'deals'}/{row.name}"
	return rows, total


def _new_today() -> tuple[list, int]:
	conditions, values = _where({})
	conditions += ["c.is_group = 0", "c.chat_id not like '%%@g.us'", "c.creation >= %(today)s"]
	values["today"] = f"{today()} 00:00:00"
	where = " and ".join(conditions)
	rows = frappe.db.sql(
		f"""select {CHAT_COLUMNS}, c.creation from `tabPeriskope Chat` c where {where}
		order by c.creation desc limit {LIMIT}""",
		values,
		as_dict=True,
	)
	total = frappe.db.sql(f"select count(*) from `tabPeriskope Chat` c where {where}", values)[0][0]
	rows = _decorate(rows)
	welcomed = set(
		frappe.get_all(
			"Periskope Auto Reply",
			filters={"chat_id": ["in", [r.chat_id for r in rows] or [""]], "kind": "Welcome"},
			pluck="chat_id",
		)
	)
	for row in rows:
		row.welcomed = row.chat_id in welcomed
	return rows, total


@frappe.whitelist(methods=["POST"])
def complete_task(task: str | int) -> None:
	"""Mark one of the rep's tasks done. CRM Task names are numbers (autoincrement)."""
	access.require_inbox_user()
	doc = frappe.get_doc("CRM Task", task)
	if doc.assigned_to != frappe.session.user and not doc.has_permission("write"):
		frappe.throw(_("You cannot change this task."), frappe.PermissionError)
	doc.status = "Done"
	doc.save(ignore_permissions=True)
