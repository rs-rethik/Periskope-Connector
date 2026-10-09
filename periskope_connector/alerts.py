"""Alert management when a message is classified as high severity.

Sent once per message, by email and as a system notification, to the Manager Emails in Periskope
Settings (the same list that gets the missed-reply escalation). A manager who is also a Frappe user
gets the notification bell as well.
"""

import re

import frappe
from frappe import _
from frappe.utils import escape_html, get_url_to_form

from periskope_connector.reminders import get_config
from periskope_connector.tags import default_tag


def _users(emails: list[str]) -> list[str]:
	return frappe.get_all("User", filters={"email": ["in", emails], "enabled": 1}, pluck="name")


def _who(doc) -> str:
	if doc.is_group:
		return doc.chat_name or _("a WhatsApp group")
	return doc.sender_name or doc.contact_number or ""


def _recipients(rows: list) -> list[str]:
	"""The emails on the matching tag actions; a row with none means the Manager Emails."""
	emails: list[str] = []
	for row in rows:
		listed = [e.strip() for e in re.split(r"[,\n;]", row.get("recipients") or "") if e.strip()]
		emails += listed or get_config().emails
	return list(dict.fromkeys(emails))


def send_alert(message: str, rows: list) -> bool:
	"""Email the recipients of the tag's Email Alert actions about a message. Returns True when an
	alert went out. One alert per message, however many rows match.

	Never raises: a problem here must not undo the message's classification."""
	try:
		doc = frappe.get_doc("Periskope Message", message)
		if doc.high_alert_sent:
			return False
		emails = _recipients(rows)
		if not emails:
			return False

		category = (doc.ai_category or default_tag()).replace("_", " ")
		summary = doc.ai_summary or ""
		rep = doc.rep or doc.org_phone or _("no rep assigned")
		link = get_url_to_form("Periskope Message", doc.name)
		subject = _("{0} severity WhatsApp message: {1}").format(doc.severity or "Low", category)
		crm = ""
		if doc.crm_deal or doc.crm_lead:
			doctype, name = ("CRM Deal", doc.crm_deal) if doc.crm_deal else ("CRM Lead", doc.crm_lead)
			crm = f"<p>{_('CRM')}: <a href='{get_url_to_form(doctype, name)}'>{escape_html(name)}</a></p>"

		frappe.sendmail(
			recipients=emails,
			subject=subject,
			message=(
				f"<p><b>{escape_html(category)}</b> &ndash; {escape_html(summary)}</p>"
				f"<p>{_('Customer')}: {escape_html(_who(doc))}<br>{_('Rep')}: {escape_html(str(rep))}</p>"
				f"<p>{_('Message')}: {escape_html((doc.body or '')[:300])}</p>{crm}"
				f"<p><a href='{link}'>{_('Open the message')}</a></p>"
			),
			reference_doctype="Periskope Message",
			reference_name=doc.name,
		)
		for user in _users(emails):
			frappe.get_doc(
				{
					"doctype": "Notification Log",
					"type": "Alert",
					"for_user": user,
					"subject": f"{subject}: {summary}"[:140],
					"document_type": "Periskope Message",
					"document_name": doc.name,
				}
			).insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Message", message, "high_alert_sent", 1, update_modified=False)
		return True
	except Exception:
		frappe.log_error(title=f"Periskope: could not send the high severity alert for {message}")
		return False
