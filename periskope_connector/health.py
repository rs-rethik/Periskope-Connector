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
		frappe.sendmail(
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
		"Periskope Phone",
		filters={"parent": "Periskope Settings"},
		fields=["number", "phone_name", "rep"],
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


def spend_check() -> bool:
	"""Scheduler, daily: tell the managers when the month's token budget is spent."""
	if not budget_exceeded() or _alerted_recently("budget"):
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
	"""message.created logs whose message never landed, ignoring duplicates that were
	correctly rejected by the message_id guard."""
	row = frappe.db.sql(
		"""select count(*) from `tabPeriskope Webhook Log` l
		left join `tabPeriskope Message` m on m.webhook_log = l.name
		where l.event_type = 'message.created' and l.signature_valid = 1 and m.name is null
		and not exists (
			select 1 from `tabPeriskope Message` pm
			where pm.message_id = json_unquote(json_extract(l.payload, '$.data.message_id'))
		)"""
	)
	return int(row[0][0]) if row else 0


def get_health() -> dict:
	"""The live figures shown on the settings page."""
	phones = phone_counts()
	return {
		"last_webhook": str(
			frappe.db.get_value("Periskope Webhook Log", {"creation": ["is", "set"]}, "max(creation)")
			or _("never")
		),
		"last_ai_run": str(
			frappe.db.get_value("Periskope Message", {"classified_at": ["is", "set"]}, "max(classified_at)")
			or _("never")
		),
		"pending": frappe.db.count("Periskope Message", {"classification_status": "Pending"}),
		"failed": frappe.db.count("Periskope Message", {"classification_status": "Failed"}),
		"unprocessed": _unprocessed_logs(),
		"month_tokens": month_tokens(),
		"phones": ", ".join(
			f"{number}: {stats.today} ({_('avg')} {stats.baseline})" for number, stats in phones.items()
		)
		or _("no messages in the last 15 days"),
		"today": nowdate(),
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
		frappe.sendmail(
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
