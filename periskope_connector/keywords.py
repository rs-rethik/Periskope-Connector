"""Keyword alerts: notify management when an incoming message contains configured words,
regardless of what the AI makes of it.

Rules live in Periskope Settings (child table). Matching is whole-word, case-insensitive, over
the message text; a dedupe window stops one long argument from sending one alert per message.
Nothing here sends WhatsApp messages: alerts go out as email and as Frappe notifications.
"""

import re
from datetime import timedelta

import frappe
from frappe import _
from frappe.utils import escape_html, get_url_to_form, now_datetime

from periskope_connector.system import insert_as_system

DEFAULT_DEDUPE_HOURS = 6

# The message fields the check and the alert email read.
MESSAGE_FIELDS = [
	"name",
	"direction",
	"body",
	"chat_id",
	"chat_name",
	"sender_name",
	"contact_number",
	"ai_category",
	"rep",
	"org_phone",
	"crm_lead",
	"crm_deal",
]


def _words(text: str | None) -> list[str]:
	seen: set[str] = set()
	words = []
	for word in re.split(r"[,\n;]", text or ""):
		word = word.strip()
		if word and word.lower() not in seen:
			seen.add(word.lower())
			words.append(word)
	return words


def _edge(char: str, at_start: bool) -> str:
	"""A boundary that works next to any character: \\b next to a word character, and a
	lookaround otherwise ('p.o.' must match 'the P.O. today' but not 'p.o.box')."""
	if re.match(r"\w", char, flags=re.UNICODE):
		return r"\b"
	return r"(?<!\w)" if at_start else r"(?!\w)"


def pattern_for(words: list[str]) -> re.Pattern:
	"""One case-insensitive regex matching any of the words as whole words ('PO.' and 'PO-123'
	match 'po'; 'post' does not). Longer words are tried first so phrases win over their parts."""
	parts = []
	for word in sorted(words, key=len, reverse=True):
		escaped = re.escape(word)
		parts.append(f"{_edge(word[0], at_start=True)}{escaped}{_edge(word[-1], at_start=False)}")
	return re.compile("|".join(parts), flags=re.IGNORECASE | re.UNICODE)


def rules() -> list[frappe._dict]:
	"""Enabled rules with recipients, ready to run."""
	rows = frappe.get_all(
		"Periskope Keyword Rule",
		filters={"parent": "Periskope Settings", "parenttype": "Periskope Settings", "enabled": 1},
		fields=["name", "rule_name", "keywords", "recipients"],
	)
	out = []
	for row in rows:
		words = _words(row.keywords)
		recipients = [e.strip() for e in re.split(r"[,\n;]", row.recipients or "") if e.strip()]
		if not words or not recipients:
			continue  # a rule without recipients matches but sends nothing
		out.append(
			frappe._dict(
				name=row.name,
				rule_name=row.rule_name,
				pattern=pattern_for(words),
				recipients=recipients,
			)
		)
	return out


def _recently_alerted(rule_name: str, chat_id: str, window_hours: int) -> bool:
	return bool(
		frappe.db.exists(
			"Periskope Keyword Alert",
			{
				"rule_name": rule_name,
				"chat_id": chat_id,
				"sent_at": [">", now_datetime() - timedelta(hours=window_hours)],
			},
		)
	)


def _users(emails: list[str]) -> list[str]:
	return frappe.get_all("User", filters={"email": ["in", emails], "enabled": 1}, pluck="name")


def _notify(doc, rule, window_hours: int) -> str | None:
	"""Email the rule's recipients and notify its Frappe users. Returns the alert row name."""
	who = doc.chat_name or doc.sender_name or doc.contact_number or ""
	category = (doc.ai_category or "").replace("_", " ")
	subject = _("WhatsApp keyword alert ({0}): {1}").format(rule.rule_name, who)
	crm = ""
	if doc.crm_deal or doc.crm_lead:
		doctype, name = ("CRM Deal", doc.crm_deal) if doc.crm_deal else ("CRM Lead", doc.crm_lead)
		crm = f"<p>{_('CRM')}: <a href='{get_url_to_form(doctype, name)}'>{escape_html(name)}</a></p>"
	try:
		frappe.sendmail(
			recipients=rule.recipients,
			subject=subject,
			message=(
				f"<p><b>{escape_html(rule.rule_name)}</b> matched in a message from "
				f"<b>{escape_html(who)}</b>.</p>"
				f"<p>{_('Rep')}: {escape_html(doc.rep or doc.org_phone or '-')}<br>"
				f"{_('AI category')}: {escape_html(category)}</p>"
				f"<p>{_('Message')}: {escape_html((doc.body or '')[:300])}</p>{crm}"
				f"<p><a href='{get_url_to_form('Periskope Message', doc.name)}'>"
				f"{_('Open the message')}</a> "
				f"({_('more alerts in this chat are suppressed for {0} hours').format(window_hours)})</p>"
			),
			reference_doctype="Periskope Message",
			reference_name=doc.name,
		)
	except Exception:
		frappe.log_error(title=f"Periskope: could not email the keyword alert for {doc.name}")
		return None

	for user in _users(rule.recipients):
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"type": "Alert",
				"for_user": user,
				"subject": f"{subject}"[:140],
				"document_type": "Periskope Message",
				"document_name": doc.name,
			}
		).insert(ignore_permissions=True)

	return insert_as_system(
		{
			"doctype": "Periskope Keyword Alert",
			"rule": rule.name,
			"rule_name": rule.rule_name,
			"message": doc.name,
			"chat_id": doc.chat_id,
			"contact_number": doc.contact_number,
			"sent_at": now_datetime(),
			"sent_to": ", ".join(rule.recipients),
		}
	).name


def check_message(message: str) -> int:
	"""Run every enabled rule over one message. Returns how many alerts went out.

	Never raises: an alerting problem must not stop the message flow."""
	try:
		doc = frappe.db.get_value("Periskope Message", message, MESSAGE_FIELDS, as_dict=True)
		if not doc or doc.direction != "Incoming" or not (doc.body or "").strip():
			return 0
		window = (
			frappe.db.get_single_value("Periskope Settings", "keyword_dedupe_hours") or DEFAULT_DEDUPE_HOURS
		)
		sent = 0
		for rule in rules():
			if not rule.recipients:
				continue  # defence in depth: rules() already filters, keep it true here too
			if not rule.pattern.search(doc.body):
				continue
			if _recently_alerted(rule.rule_name, doc.chat_id, window):
				continue
			if _notify(doc, rule, window):
				sent += 1
		return sent
	except Exception:
		frappe.log_error(title=f"Periskope: keyword check failed for message {message}")
		return 0
