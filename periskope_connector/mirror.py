"""Show the WhatsApp conversation in the CRM's own WhatsApp tab on the lead or deal.

The CRM's tab reads "WhatsApp Message" rows (from the frappe_whatsapp app) linked to the record and
is shown when the default WhatsApp account is Active. This app adds an account named "Periskope"
(it has no Meta credentials and never talks to Meta) and copies every Periskope message of a linked
chat into a row of that account:

- written with db_insert, so frappe_whatsapp's send code and the CRM's reference rewrite never run,
- dated with the WhatsApp time, so late or recovered messages land in order,
- the assistant's own messages start with "AI assistant:" (the tab shows only the text),
- the rep's reply typed in the tab is sent through Periskope by overrides/whatsapp_message.py.

Test mode: only allowed chats are copied (the CRM records of other chats are not touched).
"""

from datetime import timedelta

import frappe
from frappe.model.naming import set_new_name
from frappe.utils import get_datetime, now_datetime

from periskope_connector import safety
from periskope_connector.text_rules import readable_text

ACCOUNT = "Periskope"
AI_PREFIX = "AI assistant: "
FRESH_MINUTES = 30  # older messages (recovered by the backfill) do not notify the rep
FIELDS = [
	"name",
	"chat_id",
	"direction",
	"body",
	"message_type",
	"periskope_type",
	"message_timestamp",
	"contact_number",
	"org_phone",
	"crm_lead",
	"crm_deal",
	"message_id",
	"agent_reply",
	"media_path",
	"ack",
	"whatsapp_message",
	"transcript",
	"is_deleted",
]
CONTENT_TYPES = {"text": "text", "image": "image", "document": "document", "audio": "audio", "video": "video"}
ACK_STATUS = {"2": "delivered", "3": "read", "4": "read"}


def available() -> bool:
	return bool(
		frappe.db.exists("DocType", "WhatsApp Message") and frappe.db.exists("WhatsApp Account", ACCOUNT)
	)


def ensure_account():
	"""Add the Periskope WhatsApp account (install / migrate) so the CRM shows its WhatsApp tab.
	It becomes the default only when no other account is the default, so a real Meta account that
	is already in use keeps working."""
	if not frappe.db.exists("DocType", "WhatsApp Account"):
		return
	if not frappe.db.exists("WhatsApp Account", ACCOUNT):
		taken = frappe.db.exists("WhatsApp Account", {"is_default_outgoing": 1})
		frappe.get_doc(
			{
				"doctype": "WhatsApp Account",
				"account_name": ACCOUNT,
				"status": "Active",
				"url": "https://periskope.invalid",
				"version": "v0",
				"phone_id": "periskope",
				"token": "not-used",
				"is_default_incoming": 0 if taken else 1,
				"is_default_outgoing": 0 if taken else 1,
			}
		).insert(ignore_permissions=True)
	if frappe.db.exists("DocType", "WhatsApp Settings"):
		for field in ("default_outgoing_account", "default_incoming_account"):
			if not frappe.db.get_single_value("WhatsApp Settings", field):
				frappe.db.set_single_value("WhatsApp Settings", field, ACCOUNT)


def _text(m) -> str:
	if m.is_deleted:
		return "This message was deleted"
	body = readable_text(m) or f"[{m.message_type or 'attachment'}]"
	return (AI_PREFIX + body) if m.agent_reply else body


def _values(m, reference: tuple[str, str]) -> dict:
	incoming = m.direction == "Incoming"
	customer = f"+{m.contact_number}" if m.contact_number else ""
	values = {
		"type": "Incoming" if incoming else "Outgoing",
		"from": customer if incoming else "",
		"to": f"+{m.org_phone}" if incoming and m.org_phone else ("" if incoming else customer),
		"message": _text(m),
		"content_type": CONTENT_TYPES.get(m.message_type or "text", "text"),
		"message_type": "Manual",
		"message_id": m.message_id,
		"whatsapp_account": ACCOUNT,
		"status": "" if incoming else ACK_STATUS.get(str(m.ack or ""), "sent"),
		"label": {"Answer": "AI reply", "Holding": "AI holding message"}.get(m.agent_reply or "", ""),
		"reference_doctype": reference[0],
		"reference_name": reference[1],
	}
	if (m.media_path or "").startswith(("http://", "https://")):
		values["attach"] = m.media_path
	return values


def _announce(doc, notify: bool):
	"""What the CRM does after a WhatsApp Message is saved: refresh the open tab, and tell the
	assigned reps about a new customer message."""
	frappe.publish_realtime(
		"whatsapp_message",
		{"reference_doctype": doc.reference_doctype, "reference_name": doc.reference_name},
		after_commit=True,
	)
	if notify:
		try:
			from crm.api.whatsapp import notify_agent
		except ImportError:
			return
		notify_agent(doc)


def upsert(message: str) -> str | None:
	"""Copy one Periskope Message into the CRM WhatsApp tab, or update its copy. Returns the
	WhatsApp Message name. Never raises: the copy must not stop a message from being saved."""
	try:
		if not available():
			return None
		m = frappe.db.get_value("Periskope Message", message, FIELDS, as_dict=True)
		if not m or not m.chat_id:
			return None
		reference = (
			("CRM Deal", m.crm_deal) if m.crm_deal else ("CRM Lead", m.crm_lead) if m.crm_lead else None
		)
		if not reference or safety.crm_mode(m.chat_id) != safety.LIVE:
			return None
		values = _values(m, reference)
		existing = m.whatsapp_message or (
			m.message_id and frappe.db.get_value("WhatsApp Message", {"message_id": m.message_id})
		)
		if existing and frappe.db.exists("WhatsApp Message", existing):
			keep = ("message", "status", "label", "reference_doctype", "reference_name")
			frappe.db.set_value(
				"WhatsApp Message", existing, {k: values[k] for k in keep}, update_modified=False
			)
			name = existing
			_announce(frappe._dict(values), notify=False)
		else:
			doc = frappe.new_doc("WhatsApp Message")
			doc.update(values)
			stamp = m.message_timestamp or now_datetime()
			doc.creation = doc.modified = stamp
			doc.owner = doc.modified_by = "Administrator"
			doc.flags.periskope_mirror = True
			set_new_name(doc)
			doc.db_insert()
			name = doc.name
			fresh = get_datetime(stamp) > now_datetime() - timedelta(minutes=FRESH_MINUTES)
			_announce(doc, notify=m.direction == "Incoming" and fresh)
		if m.whatsapp_message != name:
			frappe.db.set_value("Periskope Message", message, "whatsapp_message", name, update_modified=False)
		return name
	except Exception:
		frappe.log_error(title="Periskope: could not show the message in the CRM WhatsApp tab")
		return None
