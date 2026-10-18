"""The figures behind the Flow tab of Periskope Settings: one entry per step of the pipeline with its
health (ok, warn, bad or off) and a few numbers. Everything here is read-only, and every number comes
from data the app already keeps. The health colours follow the same rules as the Health tab."""

import frappe
from frappe import _
from frappe.utils import add_days, get_datetime, now_datetime, nowdate

from periskope_connector import actions, health, query, safety
from periskope_connector.setup_status import feature_on

# The action columns of the tag matrix, and the rows a tick adds. Email alerts start at High so a
# new tick does not mail the team about every message.
TAG_ACTIONS = [
	("Create Lead", {"min_severity": "Low", "chat_type": "1-to-1"}),
	(actions.EMAIL_ALERT, {"min_severity": "High", "chat_type": "Any"}),
	(actions.TIMELINE_COMMENT, {"min_severity": "Low", "chat_type": "Any"}),
	(actions.TODO_FOR_REP, {"min_severity": "Low", "chat_type": "Any"}),
]
OWN_DOCTYPES = ("Periskope Message", "Periskope Reply Reminder")

# What the Flow dialog may change in the settings, and in the SLA levels and keyword rules (their own
# doctypes; a row is addressed by its name). The dialog saves these straight to the server, so the
# settings form behind it is not touched while it is open.
SETTING_FIELDS = (
	"enabled",
	"enable_classification",
	"classify_group_chats",
	"create_leads_from_enquiries",
	"ignore_suppliers",
	"ignore_employees",
	"enable_reply_reminders",
	"remind_group_chats",
	"reps_see_own_numbers",
	"comment_mode",
	"working_days",
	"working_hours_start",
	"working_hours_end",
	"alert_emails",
	"escalation_emails",
	"enable_agent",
	"enable_auto_reply",
	"reply_delay_minutes",
	"holding_message",
)
ROW_FIELDS = {
	"sla_levels": ("wait_minutes", "min_severity", "action", "recipients"),
	"keyword_rules": ("recipients", "enabled"),
}
ROW_DOCTYPES = {"sla_levels": "Periskope SLA Level", "keyword_rules": "Periskope Keyword Rule"}


def _levels() -> list:
	return frappe.get_all(
		"Periskope SLA Level",
		fields=["name", "wait_minutes", "min_severity", "action", "recipients"],
		order_by="wait_minutes asc, creation asc",
	)


def _rules() -> list:
	return frappe.get_all(
		"Periskope Keyword Rule",
		fields=["name", "rule_name", "recipients", "enabled"],
		order_by="rule_name asc",
	)


def _ago(moment) -> str:
	if not moment:
		return ""
	return frappe.utils.pretty_date(moment)


def _status(bad=False, warn=False, off=False) -> str:
	return "off" if off else "bad" if bad else "warn" if warn else "ok"


def _count(doctype: str, filters: dict | None = None) -> int:
	return frappe.db.count(doctype, filters or {})


def _periskope(settings) -> dict:
	phones = [
		{
			"name": p.phone_name or p.number,
			"number": p.number,
			"state": p.wa_state or "",
			"ready": bool(p.is_ready),
			"rep": p.rep,
		}
		for p in settings.phones
	]
	failed = (settings.connection_status or "").startswith("Failed")
	ready = sum(1 for p in phones if p["ready"])
	return {
		"status": _status(bad=failed or not phones, warn=ready < len(phones)),
		"headline": (
			_("{0} of {1} number(s) ready").format(ready, len(phones)) if phones else _("No number linked")
		),
		"connection": settings.connection_status or _("Not tested"),
		"phones": phones,
	}


def _capture(settings) -> dict:
	last = query.latest("Periskope Webhook Log", "creation")
	today = _count("Periskope Message", {"creation": [">=", nowdate()]})
	recovered = _count(
		"Periskope Message",
		{"creation": [">=", add_days(now_datetime(), -1)], "webhook_log": ["is", "not set"]},
	)
	unprocessed = health._unprocessed_logs()
	stale = bool(last) and (now_datetime() - get_datetime(last)).total_seconds() > 24 * 3600
	return {
		"status": _status(off=not settings.enabled, warn=unprocessed > 0 or stale or not last),
		"headline": _("{0} today").format(today),
		"last_webhook": _ago(last) or _("never"),
		"today": today,
		"recovered": recovered,
		"unprocessed": unprocessed,
		"events": [row.event for row in settings.webhook_events],
	}


def _link() -> dict:
	counts = {
		s: _count("Periskope Message", {"link_status": s})
		for s in ("Linked", "Internal", "Ambiguous", "No Match")
	}
	return {
		"status": _status(warn=counts["Ambiguous"] > 0),
		"headline": _("{0} linked").format(counts["Linked"]),
		"counts": counts,
	}


def _queue(settings) -> dict:
	new = _count("Periskope Contact", {"status": "New"})
	return {
		"status": _status(warn=new > 0),
		"headline": _("{0} new").format(new),
		"new": new,
		"lead_created": _count("Periskope Contact", {"status": "Lead Created"}),
		"ignored": _count("Periskope Contact", {"status": ["like", "Ignored%"]}),
	}


def _keywords(settings) -> dict:
	rules = [
		{"name": r.name, "rule": r.rule_name, "recipients": r.recipients or "", "enabled": bool(r.enabled)}
		for r in _rules()
	]
	silent = [r for r in rules if r["enabled"] and not r["recipients"].strip()]
	return {
		"status": _status(warn=bool(silent), off=not rules),
		"headline": _("{0} rule(s)").format(len(rules)),
		"rules": rules,
		"alerts_today": _count("Periskope Keyword Alert", {"creation": [">=", nowdate()]}),
	}


def _ai(settings) -> dict:
	pending = _count("Periskope Message", {"classification_status": "Pending"})
	failed = _count("Periskope Message", {"classification_status": "Failed"})
	tagged = _count("Periskope Message", {"classification_status": "Done"})
	corrected = _count("Periskope Message", {"correct_category": ["is", "set"]})
	return {
		"status": _status(
			off=not feature_on("enable_classification", settings), warn=failed > 0 or pending > 50
		),
		"headline": _("{0} waiting, {1} failed").format(pending, failed),
		"model": settings.ai_model or "",
		"pending": pending,
		"failed": failed,
		"tagged": tagged,
		"corrected": corrected,
		"tokens": health.month_tokens(),
	}


def _tags() -> dict:
	tags = frappe.get_all(
		"Periskope Tag", fields=["name", "active", "is_default"], order_by="sort_order asc, creation asc"
	)
	rows = frappe.get_all(
		"Periskope Tag Action", filters={"parenttype": "Periskope Tag"}, fields=["parent", "action"]
	)
	have = {(r.parent, r.action) for r in rows}
	used = {
		r.ai_category: r.n
		for r in query.counts("Periskope Message", ["ai_category"], {"ai_category": ["is", "set"]})
	}
	out = [
		{
			"name": t.name,
			"active": bool(t.active),
			"default": bool(t.is_default),
			"messages": used.get(t.name, 0),
			"actions": {a: (t.name, a) in have for a, _defaults in TAG_ACTIONS},
		}
		for t in tags
	]
	default = next((t["name"] for t in out if t["default"]), "")
	return {
		"status": _status(warn=not any(t["active"] for t in out)),
		"headline": _("{0} tag(s)").format(sum(1 for t in out if t["active"])),
		"tags": out,
		"default": default,
		"action_names": [a for a, _defaults in TAG_ACTIONS],
		"rows": len(rows),
	}


def _waiting(settings) -> dict:
	waiting = _count("Periskope Reply Reminder", {"status": ["in", ["Open", "Escalated"]]})
	return {
		"status": _status(off=not feature_on("enable_reply_reminders", settings)),
		"headline": _("{0} waiting now").format(waiting),
		"waiting": waiting,
	}


def _sla(settings) -> dict:
	open_rows = frappe.get_all(
		"Periskope Reply Reminder", filters={"status": ["in", ["Open", "Escalated"]]}, fields=["level"]
	)
	deep = sum(1 for r in open_rows if (r.level or 0) >= 2)
	levels = [
		{
			"name": r.name,
			"wait": r.wait_minutes,
			"severity": r.min_severity,
			"action": r.action,
			"recipients": r.recipients or "",
		}
		for r in _levels()
	]
	return {
		"status": _status(
			off=not feature_on("enable_reply_reminders", settings), bad=not levels, warn=deep > 0
		),
		"headline": _("{0} escalated").format(deep),
		"levels": levels,
		"open": len(open_rows),
		"deep": deep,
	}


def _people(settings) -> dict:
	emails = _count(
		"Email Queue", {"creation": [">=", nowdate()], "reference_doctype": ["in", list(OWN_DOCTYPES)]}
	)
	mail_ready = bool(frappe.db.exists("Email Account", {"default_outgoing": 1, "enable_outgoing": 1}))
	return {
		"status": _status(bad=not mail_ready, warn=not (settings.alert_emails or "").strip()),
		"headline": _("{0} email(s) today").format(emails),
		"emails_today": emails,
		"mail_ready": mail_ready,
	}


def _autoreply(settings) -> dict:
	today = {
		r.status: r.n
		for r in query.counts("Periskope Auto Reply", ["status"], {"modified": [">=", nowdate()]})
	}
	paused = _count("Periskope Chat", {"ai_paused": 1})
	return {
		"status": _status(off=not feature_on("enable_auto_reply", settings), warn=today.get("Failed", 0) > 0),
		"headline": _("{0} answered, {1} holding today").format(
			today.get("Sent", 0), today.get("Holding Sent", 0)
		),
		"today": today,
		"waiting": _count("Periskope Auto Reply", {"status": "Waiting"}),
		"paused": paused,
	}


def _crm(settings) -> dict:
	rows = query.counts(
		"Periskope Agent Action",
		["action", "mode"],
		{
			"creation": [">=", nowdate()],
			"action": [
				"in",
				["Create Note", "Update Note", "Create Task", "Update Task", "Status Change", "Timeline"],
			],
		},
	)
	live = {r.action: r.n for r in rows if r.mode == "Live"}
	dry = sum(r.n for r in rows if r.mode != "Live")
	notes = live.get("Create Note", 0) + live.get("Update Note", 0)
	tasks = live.get("Create Task", 0) + live.get("Update Task", 0)
	return {
		"status": _status(off=not settings.enable_agent, warn=dry > 0),
		"headline": _("{0} notes, {1} tasks today").format(notes, tasks),
		"notes": notes,
		"tasks": tasks,
		"status_changes": live.get("Status Change", 0),
		"timeline": live.get("Timeline", 0),
		"dry_runs": dry,
	}


def _agent(settings) -> dict:
	last = frappe.get_all(
		"Periskope Agent Run",
		filters={"status": ["!=", "Skipped"]},
		fields=["status", "started_on", "messages_read", "suggestions_added", "error"],
		order_by="started_on desc",
		limit=1,
	)
	last = last[0] if last else None
	pending = _count("Periskope Suggestion", {"status": "Pending"})
	return {
		"status": _status(
			off=not settings.enable_agent, bad=bool(last and last.status == "Failed"), warn=pending > 0
		),
		"headline": _("{0} waiting for approval").format(pending),
		"last_run": _ago(last.started_on) if last else _("never"),
		"last_status": last.status if last else "",
		"last_error": (last.error or "")[:200] if last else "",
		"pending": pending,
		"knowledge": _count("Periskope Knowledge Entry", {"status": "Approved"}),
		"test_mode": safety.test_mode(),
	}


def _settings_values(settings) -> dict:
	"""The settings the panels show and edit, so the dialog never reads the open form."""
	values = {name: settings.get(name) for name in SETTING_FIELDS}
	for name in ("working_hours_start", "working_hours_end"):
		values[name] = str(values[name] or "")
	values["sla_levels"] = [
		{
			"idx": r.name,
			"wait_minutes": r.wait_minutes,
			"min_severity": r.min_severity,
			"action": r.action,
			"recipients": r.recipients or "",
		}
		for r in _levels()
	]
	values["keyword_rules"] = [
		{"idx": r.name, "rule_name": r.rule_name, "recipients": r.recipients or "", "enabled": r.enabled}
		for r in _rules()
	]
	return values


def get_flow() -> dict:
	"""Every step of the pipeline with its health and figures, for the Flow tab."""
	settings = frappe.get_doc("Periskope Settings")
	tags = _tags()
	return {
		"settings": _settings_values(settings),
		"periskope": _periskope(settings),
		"capture": _capture(settings),
		"link": _link(),
		"queue": _queue(settings),
		"keywords": _keywords(settings),
		"ai": _ai(settings),
		"tags": tags,
		"actions": tags,
		"waiting": _waiting(settings),
		"sla": _sla(settings),
		"people": _people(settings),
		"autoreply": _autoreply(settings),
		"crm": _crm(settings),
		"agent": _agent(settings),
	}


def set_tag_active(tag: str, active: bool):
	"""Switch a tag on or off. The tag's own checks refuse to switch off the default or the last one."""
	doc = frappe.get_doc("Periskope Tag", tag)
	doc.active = 1 if active else 0
	doc.save()


def set_tag_action(tag: str, action: str, on: bool):
	"""Give a tag an action (a row with the usual severity and chat type) or take every row of it away."""
	defaults = dict(TAG_ACTIONS).get(action)
	if defaults is None:
		frappe.throw(_("Unknown action {0}").format(action))
	doc = frappe.get_doc("Periskope Tag", tag)
	rows = [r for r in doc.actions if r.action == action]
	if on and not rows:
		doc.append("actions", {"action": action, **defaults})
	elif not on and rows:
		doc.set("actions", [r for r in doc.actions if r.action != action])
	else:
		return
	doc.save()


def set_setting(field: str, value):
	"""Change one setting from the Flow dialog and save it (the settings' own checks still run)."""
	if field not in SETTING_FIELDS:
		frappe.throw(_("{0} cannot be changed here").format(field))
	settings = frappe.get_doc("Periskope Settings")
	settings.set(field, value)
	settings.save()


def set_row(table: str, idx: str, field: str, value):
	"""Change one field of an SLA level or a keyword rule (`idx` is its name), and save."""
	if field not in ROW_FIELDS.get(table, ()):
		frappe.throw(_("{0} cannot be changed here").format(field))
	doctype = ROW_DOCTYPES[table]
	if not frappe.db.exists(doctype, idx):
		frappe.throw(_("Row {0} does not exist").format(idx))
	doc = frappe.get_doc(doctype, idx)
	doc.set(field, value)
	doc.save()


def add_sla_level():
	"""A new SLA step with the usual first values; the team edits it afterwards."""
	frappe.get_doc(
		{"doctype": "Periskope SLA Level", "wait_minutes": 60, "min_severity": "Low", "action": "Notify Rep"}
	).insert()


def remove_sla_level(idx: str):
	frappe.delete_doc("Periskope SLA Level", idx)
