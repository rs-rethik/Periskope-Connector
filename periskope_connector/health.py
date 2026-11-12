"""Operational health: is the WhatsApp capture actually working?

Two watchers:
- sync_check (daily): a linked number that normally has traffic but received nothing in the last
  24 h has probably unlinked from Periskope. That is the silent failure of "management sees every
  conversation" — nobody notices until it is too late, so the app must notice itself.
- spend_check (daily, and before each classification batch): the monthly AI token budget.

Plus get_health(), the numbers shown on the settings page's Health tab.
"""

import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import now_datetime, nowdate

from periskope_connector import query, safety

# A number with at least this much average daily traffic is expected to say something today.
MIN_DAILY_TRAFFIC = 3
ALERT_AGAIN_AFTER_HOURS = 20  # one alert per silent phone per day, not per run


def _manager_emails() -> list[str]:
	settings = frappe.get_cached_doc("Periskope Settings")
	text = settings.escalation_emails or settings.alert_emails or ""
	emails = [e.strip() for e in text.replace("\n", ",").split(",") if e.strip()]
	return emails


def _alerted_recently(key: str) -> bool:
	# expires=True: read straight from Redis. get_value() would otherwise cache a miss in
	# frappe.local.cache, and set_value(expires_in_sec=...) never updates that local entry.
	return frappe.cache().get_value(f"periskope_alert:{key}", expires=True) is not None


def _mark_alerted(key: str):
	frappe.cache().set_value(f"periskope_alert:{key}", 1, expires_in_sec=ALERT_AGAIN_AFTER_HOURS * 3600)


def _send(subject: str, message: str, reference_doctype=None, reference_name=None) -> bool:
	emails = _manager_emails()
	if not emails:
		return False
	try:
		safety.sendmail(
			recipients=emails,
			subject=subject,
			message=message,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
		)
	except Exception:
		frappe.log_error(title=f"Periskope: could not send the health alert ({subject[:60]})")
		return False
	return True


# --- Per-phone message counts ---------------------------------------------------------------------------


def phone_counts() -> dict[str, frappe._dict]:
	"""{number: {today, baseline}} — messages in the last 24 h and the prior 14-day daily average."""
	now = now_datetime()
	rows = frappe.db.sql(
		"""select org_phone,
		sum(if(message_timestamp >= %(since_today)s, 1, 0)) as today,
		sum(if(message_timestamp < %(since_today)s and message_timestamp >= %(since_baseline)s, 1, 0)) as prior
		from `tabPeriskope Message`
		where org_phone is not null and org_phone != '' and message_timestamp >= %(since_baseline)s
		group by org_phone""",
		{"since_today": now - timedelta(days=1), "since_baseline": now - timedelta(days=15)},
		as_dict=True,
	)
	out = {}
	for row in rows:
		out[row.org_phone] = frappe._dict(today=row.today or 0, baseline=round((row.prior or 0) / 14, 1))
	return out


def sync_check() -> list[str]:
	"""Scheduler, daily: email the managers about linked numbers that went silent."""
	if not frappe.db.get_single_value("Periskope Settings", "enabled"):
		return []
	counts = phone_counts()
	silent = []
	for phone in frappe.get_all(
		"WhatsApp Number",
		filters={"is_active": 1},
		fields=["number", "display_name as phone_name", "default_assignee as rep"],
	):
		stats = counts.get(phone.number)
		if not stats or stats.today > 0:
			continue
		if stats.baseline < MIN_DAILY_TRAFFIC:
			continue
		key = f"sync:{phone.number}"
		if _alerted_recently(key):
			continue
		subject = _("WhatsApp number may have unlinked: {0}").format(phone.number)
		if _send(
			subject,
			(
				f"<p>{_('The company number')} <b>{phone.number}</b> "
				f"({phone.phone_name or '-'}) {_('has received no messages in the last 24 hours,')}"
				f" {_('but it normally averages')} {stats.baseline} {_('a day. It may have unlinked from Periskope.')}</p>"
				f"<p>{_('Rep')}: {phone.rep or '-'} — <a href='https://console.periskope.app'>Periskope console</a></p>"
			),
		):
			_mark_alerted(key)
			silent.append(phone.number)
	return silent


# --- Webhook capture ---------------------------------------------------------------------------------

REJECTED_KEY = "periskope:webhook_rejected:{hour}"
REJECTED_LAST_KEY = "periskope:webhook_rejected_last"
REJECTED_ALERT_AT = 3  # rejected calls in the last hour that mean the signing secret is wrong
STALE_HOURS = 3  # working hours without any webhook, on numbers that normally get one an hour


def _hour_key(when) -> str:
	return REJECTED_KEY.format(hour=f"{when:%Y%m%d%H}")


def count_rejected_webhook():
	"""Count a webhook call rejected for its signature (api/webhook.py). Kept in Redis, not the
	database: the caller is an unknown guest, and a flood must not fill a table."""
	cache = frappe.cache()
	key = _hour_key(now_datetime())
	cache.set_value(key, (cache.get_value(key, expires=True) or 0) + 1, expires_in_sec=26 * 3600)
	cache.set_value(REJECTED_LAST_KEY, str(now_datetime()), expires_in_sec=7 * 24 * 3600)


def rejected_webhooks(hours: int = 24) -> int:
	"""Webhook calls rejected for their signature in the last `hours` hours."""
	now = now_datetime()
	cache = frappe.cache()
	return sum(cache.get_value(_hour_key(now - timedelta(hours=h)), expires=True) or 0 for h in range(hours))


def last_rejected_webhook() -> str | None:
	return frappe.cache().get_value(REJECTED_LAST_KEY, expires=True)


def capture_problem() -> str:
	"""Why messages are probably not arriving, or "": calls are being rejected (the signing secret no
	longer matches), or no webhook came for STALE_HOURS working hours although the numbers normally
	get at least one an hour."""
	if not frappe.db.get_single_value("Periskope Settings", "enabled"):
		return ""
	rejected = rejected_webhooks(1)
	if rejected >= REJECTED_ALERT_AT:
		return _(
			"{0} webhook calls from Periskope were rejected in the last hour because their signature "
			"does not match. Copy the signing secret from the Periskope console into Periskope Settings."
		).format(rejected)
	from periskope_connector.reminders import get_config, is_working_time, working_seconds

	cfg = get_config()
	now = now_datetime()
	last = query.latest("Periskope Webhook Log", "creation")
	daily = sum(stats.baseline for stats in phone_counts().values())
	if not last or not is_working_time(now, cfg) or daily < 10 * STALE_HOURS:
		return ""  # a quiet site: silence says nothing (sync_check covers each number daily)
	if working_seconds(frappe.utils.get_datetime(last), now, cfg) < STALE_HOURS * 3600:
		return ""
	return _(
		"No webhook has arrived since {0}, although your numbers normally get about {1} messages a "
		"day. Check the webhook in the Periskope console and that this site is reachable."
	).format(_when(last), int(daily))


def _held_escalations() -> list[str]:
	from periskope_connector.alerts import held

	return held()


def setup_warnings() -> list[str]:
	from periskope_connector.setup_status import warnings

	return warnings()


def capture_check() -> bool:
	"""Scheduler, every 15 minutes: email the managers when capture looks stopped (capture_problem)."""
	problem = capture_problem()
	if not problem or _alerted_recently("capture"):
		return False
	if _send(_("Periskope: WhatsApp messages may not be arriving"), f"<p>{problem}</p>"):
		_mark_alerted("capture")
		return True
	return False


# --- AI spend ------------------------------------------------------------------------------------------------


def month_tokens() -> int:
	"""Total tokens (input + output + cache) billed this calendar month."""
	row = frappe.db.sql(
		"""select coalesce(sum(input_tokens + output_tokens + cache_read_tokens
		+ cache_creation_tokens), 0) from `tabPeriskope AI Usage`
		where status = 'OK' and creation >= %(since)s""",
		{"since": now_datetime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)},
	)
	return int(row[0][0]) if row else 0


def budget_exceeded() -> bool:
	budget = frappe.db.get_single_value("Periskope Settings", "ai_monthly_budget_tokens") or 0
	return bool(budget) and month_tokens() >= budget


BUDGET_WARNINGS = (70, 90)  # % of the monthly budget at which the managers hear, once a month each


def _budget_warning() -> bool:
	"""Tell the managers once a month when the tokens pass 70% and 90% of the budget, so it can be
	raised before tagging, escalations and auto replies stop."""
	budget = frappe.db.get_single_value("Periskope Settings", "ai_monthly_budget_tokens") or 0
	if not budget:
		return False
	used = month_tokens()
	passed = [p for p in BUDGET_WARNINGS if used * 100 >= p * budget]
	if not passed or used >= budget:
		return False
	level = passed[-1]
	key = f"budget_{level}:{now_datetime():%Y%m}"
	if frappe.cache().get_value(f"periskope_alert:{key}", expires=True) is not None:
		return False
	subject = _("Periskope AI budget {0}% used").format(level)
	body = _(
		"{0:,} of the {1:,} tokens of this month's AI budget are used. When it runs out, new messages "
		"are no longer tagged or escalated and the auto reply sends holding messages. Raise it in "
		"Periskope Settings if needed."
	).format(used, budget)
	if _send(subject, f"<p>{body}</p>"):
		frappe.cache().set_value(f"periskope_alert:{key}", 1, expires_in_sec=32 * 24 * 3600)
		return True
	return False


def spend_check() -> bool:
	"""Scheduler, hourly: warn the managers at 70% and 90% of the month's token budget, and tell them
	when it is spent."""
	if not budget_exceeded():
		return _budget_warning()
	if _alerted_recently("budget"):
		return False
	subject = _("Periskope AI budget reached: tagging paused")
	if _send(
		subject,
		(
			f"<p>{_('The monthly AI token budget is used up')} ({month_tokens()} tokens). "
			f"{_('New messages are not being tagged until next month, or until the budget in ')}"
			f"<a href='/app/periskope-settings'>Periskope Settings</a> is raised.</p>"
			f"<p>{_('Messages keep arriving and linking to the CRM; only AI tagging is paused.')}</p>"
		),
	):
		_mark_alerted("budget")
		return True
	return False


# --- The Health tab ---------------------------------------------------------------------------------------


def _unprocessed_logs() -> int:
	"""Webhook events of the last days not applied after half an hour (messages.unprocessed_logs)."""
	from frappe.query_builder.functions import Count

	from periskope_connector.messages import not_applied

	log = frappe.qb.DocType("Periskope Webhook Log")
	found = (
		frappe.qb.from_(log)
		.select(Count(log.name))
		.where(not_applied(log) & (log.creation < frappe.utils.add_to_date(now_datetime(), minutes=-30)))
	).run()
	return int(found[0][0] or 0) if found else 0


def _when(value) -> str:
	"""A time for the Health tab, to the second ("2026-10-09 18:35:21"), or "never"."""
	if not value:
		return _("never")
	return str(frappe.utils.get_datetime(value).replace(microsecond=0))


def get_health() -> dict:
	"""The live figures shown on the settings page."""
	phones = phone_counts()
	return {
		"last_webhook": _when(query.latest("Periskope Webhook Log", "creation")),
		"last_ai_run": _when(query.latest("Periskope Message", "classified_at")),
		"pending": frappe.db.count("Periskope Message", {"classification_status": "Pending"}),
		"failed": frappe.db.count("Periskope Message", {"classification_status": "Failed"}),
		"held_escalations": len(_held_escalations()),
		"unprocessed": _unprocessed_logs(),
		"rejected_webhooks": rejected_webhooks(),
		"capture_problem": capture_problem(),
		"warnings": setup_warnings(),
		"month_tokens": month_tokens(),
		"phones": ", ".join(
			f"{number}: {stats.today} ({_('avg')} {stats.baseline})" for number, stats in phones.items()
		)
		or _("no messages in the last 15 days"),
		"today": nowdate(),
		**agent_health(),
	}


def agent_health() -> dict:
	"""The agent's part of the Health tab (the Flow diagram shows the same figures in more detail)."""
	from periskope_connector import flow

	settings = frappe.get_doc("Periskope Settings")
	agent, reply, crm = flow._agent(settings), flow._autoreply(settings), flow._crm(settings)
	budget = settings.ai_monthly_budget_tokens or 0
	used = month_tokens()
	problem = frappe.get_all(
		"Error Log",
		filters={"method": ["like", "Periskope%"]},
		fields=["method", "creation"],
		order_by="creation desc",
		limit=1,
	)
	return {
		"agent": (
			_("off")
			if not settings.enabled
			else _("last run {0} ({1}), {2} waiting for approval").format(
				agent["last_run"], agent["last_status"] or "-", agent["pending"]
			)
		),
		"auto_reply": _("{0} answered, {1} holding, {2} dry run, {3} failed").format(
			reply["today"].get("Sent", 0),
			reply["today"].get("Holding Sent", 0),
			reply["today"].get("Dry Run", 0) + reply["today"].get("Blocked", 0),
			reply["today"].get("Failed", 0),
		),
		"crm": _("{0} notes, {1} tasks, {2} status changes").format(
			crm["notes"], crm["tasks"], crm["status_changes"]
		),
		"budget": f"{used:,} / {budget:,} ({used * 100 // budget}%)"
		if budget
		else _("{0:,} tokens, no budget set").format(used),
		"test_mode": _("on: only the allowed chats and emails") if agent["test_mode"] else _("off"),
		"test_mode_on": bool(agent["test_mode"]),
		"last_problem": f"{problem[0].method} ({frappe.utils.pretty_date(problem[0].creation)})"
		if problem
		else _("none"),
		"has_problem": bool(problem),
	}


NOT_READY = {
	"budget": (
		"Periskope AI budget reached: the agent sends holding messages",
		"The monthly AI token budget is used up. Customers get the holding message and the reps get a "
		"task for each chat until the budget is raised in Periskope Settings or the month ends.",
	),
	"no_key": (
		"Periskope AI key missing: the agent sends holding messages",
		"No AI key is set in Periskope Settings. Customers get the holding message and the reps get a "
		"task for each chat until a key is entered.",
	),
}


def notify_admin_once(reason: str) -> bool:
	"""Tell the approvers (else the managers) once a day that the agent cannot call the AI."""
	if reason not in NOT_READY or _alerted_recently(f"not_ready:{reason}"):
		return False
	subject, body = NOT_READY[reason]
	raw = frappe.db.get_single_value("Periskope Settings", "approver_emails") or ""
	emails = [e.strip() for e in re.split(r"[,\n;]", raw) if e.strip()] or _manager_emails()
	if not emails:
		return False
	try:
		safety.sendmail(
			recipients=emails,
			subject=_(subject),
			message=f"<p>{_(body)}</p><p><a href='/app/periskope-settings'>Periskope Settings</a></p>",
		)
	except Exception:
		log_once("Periskope: could not email the AI warning")
		return False
	frappe.cache().set_value(f"periskope_alert:not_ready:{reason}", 1, expires_in_sec=24 * 3600)
	return True


def log_once(title: str, hours: int = 1) -> None:
	"""frappe.log_error at most once per `hours` for the same title, so one cause that repeats on
	every message does not flood the Error Log."""
	key = f"periskope_logged:{title}"
	if frappe.cache().get_value(key, expires=True) is not None:
		return
	frappe.cache().set_value(key, 1, expires_in_sec=hours * 3600)
	frappe.log_error(title=title)
