"""Tell management at once when the AI escalates a message.

The AI decides (classifier: escalate and escalation_reason): "Managers" emails the Manager Emails,
"MD" emails the Escalation Emails (the MD) and the managers. Sent once per message, by email and as a
bell for those who are Frappe users. Nothing here sends WhatsApp messages.

A flood of messages is not a flood of emails: one chat raises at most one alert every CHAT_GAP_MINUTES
(a rise from Managers to MD still goes out), and the site at most HOURLY_CAP an hour. A message held
back by its chat's gap counts as handled (the alert already sent points the team to that chat); one
held back by the hourly cap waits and goes out in a later hour (send_held), for it is about another
customer nobody has heard of yet.
"""

import frappe
from frappe import _
from frappe.utils import escape_html, get_url_to_form

from periskope_connector import safety
from periskope_connector.reminders import _bell_target, get_config

CHAT_GAP_MINUTES = 60
HOURLY_CAP = 20
LEVEL_RANK = {"Managers": 1, "MD": 2}

# The message fields the alert email reads.
MESSAGE_FIELDS = [
	"name",
	"chat_id",
	"high_alert_sent",
	"escalation",
	"escalation_reason",
	"ai_summary",
	"rep",
	"org_phone",
	"is_group",
	"chat_name",
	"sender_name",
	"contact_number",
	"body",
	"attachment_summary",
	"crm_lead",
	"crm_deal",
	"periskope_chat",
]


def _users(emails: list[str]) -> list[str]:
	return frappe.get_all("User", filters={"email": ["in", emails], "enabled": 1}, pluck="name")


def _who(doc) -> str:
	if doc.is_group:
		return doc.chat_name or _("a WhatsApp group")
	return doc.sender_name or doc.contact_number or ""


def recipients(level: str) -> list[str]:
	cfg = get_config()
	emails = cfg.md_emails + cfg.emails if level == "MD" else cfg.emails
	return list(dict.fromkeys(emails))


HELD = "held"  # _may_alert: over the hourly cap, to be sent later
HELD_HOURS = 24  # a held escalation older than this is no longer sent


def _may_alert(doc) -> bool | str:
	"""Whether this escalation may email now (see the module notes); counts it when it may. HELD when
	only the hourly cap stops it."""
	cache = frappe.cache()
	chat_key = f"periskope_escalated:{doc.chat_id}"
	last = cache.get_value(chat_key, expires=True)
	if last and LEVEL_RANK.get(doc.escalation, 0) <= LEVEL_RANK.get(last, 0):
		return False
	hour_key = f"periskope_escalations:{frappe.utils.now_datetime():%Y%m%d%H}"
	if (cache.get_value(hour_key, expires=True) or 0) >= HOURLY_CAP:
		from periskope_connector.health import log_once

		log_once("Periskope: escalation emails held for the next hour (too many at once)")
		return HELD
	cache.set_value(chat_key, doc.escalation, expires_in_sec=CHAT_GAP_MINUTES * 60)
	cache.set_value(hour_key, (cache.get_value(hour_key, expires=True) or 0) + 1, expires_in_sec=3600)
	return True


def escalate(message: str) -> bool:
	"""Email and notify the people the AI's escalation names. Returns True when an alert went out.

	Never raises: a problem here must not undo the message's analysis."""
	try:
		doc = frappe.db.get_value("Periskope Message", message, MESSAGE_FIELDS, as_dict=True)
		if not doc or not doc.escalation or doc.high_alert_sent:
			return False
		emails = recipients(doc.escalation)
		if not emails:
			return False
		allowed = _may_alert(doc)
		if allowed == HELD:
			return False  # high_alert_sent stays 0: send_held tries again next hour
		if not allowed:
			frappe.db.set_value("Periskope Message", message, "high_alert_sent", 1, update_modified=False)
			return False

		reason = doc.escalation_reason or doc.ai_summary or ""
		rep = doc.rep or doc.org_phone or _("no rep assigned")
		link = get_url_to_form("Periskope Message", doc.name)
		subject = _("WhatsApp escalation ({0}): {1}").format(_who(doc), reason)[:140]
		crm = ""
		if doc.crm_deal or doc.crm_lead:
			doctype, name = ("CRM Deal", doc.crm_deal) if doc.crm_deal else ("CRM Lead", doc.crm_lead)
			crm = f"<p>{_('CRM')}: <a href='{get_url_to_form(doctype, name)}'>{escape_html(name)}</a></p>"
		attachment = (
			f"<p>{_('Attachment')}: {escape_html(doc.attachment_summary)}</p>"
			if doc.attachment_summary
			else ""
		)

		safety.sendmail(
			recipients=emails,
			subject=subject,
			message=(
				f"<p><b>{escape_html(reason)}</b></p>"
				f"<p>{_('Customer')}: {escape_html(_who(doc))}<br>{_('Rep')}: {escape_html(str(rep))}</p>"
				f"<p>{_('Message')}: {escape_html((doc.body or '')[:500])}</p>{attachment}{crm}"
				f"<p><a href='{link}'>{_('Open the message')}</a></p>"
			),
			reference_doctype="Periskope Message",
			reference_name=doc.name,
		)
		from periskope_connector.access import can_see_chat

		# the bell only for those who see the chat (its number, or assigned to them)
		bell_users = [u for u in _users(emails) if doc.periskope_chat and can_see_chat(doc.periskope_chat, u)]
		for user in bell_users:
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": subject,
					**_bell_target(doc.name),  # the chat: shown in the inbox's bell
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Message", message, "high_alert_sent", 1, update_modified=False)
		return True
	except Exception:
		frappe.log_error(title=f"Periskope: could not send the escalation for {message}")
		return False


def held() -> list[str]:
	"""The escalations of the last HELD_HOURS hours that were not sent yet, oldest first."""
	now = frappe.utils.now_datetime()
	return frappe.get_all(
		"Periskope Message",
		filters={
			"escalation": ["in", list(LEVEL_RANK)],
			"high_alert_sent": 0,
			"classification_status": "Done",
			"creation": [
				"between",
				[
					frappe.utils.add_to_date(now, hours=-HELD_HOURS),
					frappe.utils.add_to_date(now, minutes=-10),
				],
			],
		},
		pluck="name",
		order_by="creation asc",
		limit=HOURLY_CAP,
	)


def send_held() -> int:
	"""Scheduler, hourly: send the escalations the hourly cap held back. Returns how many went out."""
	return sum(1 for name in held() if escalate(name))
