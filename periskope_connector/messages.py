import json
import re

import frappe
import pytz
from dateutil import parser as date_parser
from frappe.utils.data import get_system_timezone

from periskope_connector.text_rules import analysis_skip_reason

MESSAGE_CREATED = "message.created"


def _digits(chat_id: str | None) -> str:
	"""'919876512340@c.us' -> '919876512340'"""
	return re.sub(r"\D", "", (chat_id or "").split("@")[0])


def _to_site_datetime(value: str | None):
	if not value:
		return None
	try:
		dt = date_parser.parse(value)
	except (ValueError, OverflowError):
		return None
	if dt.tzinfo is None:
		dt = pytz.utc.localize(dt)
	return dt.astimezone(pytz.timezone(get_system_timezone())).replace(tzinfo=None)


def _rep_for_phone(number: str) -> str | None:
	"""The sales rep assigned to this company number in Periskope Settings."""
	if not number:
		return None
	return frappe.db.get_value(
		"Periskope Phone",
		{"parent": "Periskope Settings", "number": number},
		"rep",
	)


def create_message(payload: dict, webhook_log: str | None = None) -> str | None:
	"""Create a Periskope Message from a message.created webhook payload.

	Returns the new document name, or None when the message was already saved
	or the payload has no message id.
	"""
	data = payload.get("data") or {}
	message_id = data.get("message_id")
	if not message_id or frappe.db.exists("Periskope Message", {"message_id": message_id}):
		return None

	chat_id = data.get("chat_id") or ""
	from_me = bool(data.get("from_me"))
	is_group = chat_id.endswith("@g.us")
	media = data.get("media") or {}
	org_phone = _digits(data.get("org_phone"))

	classify = bool(frappe.db.get_single_value("Periskope Settings", "enable_classification"))
	is_text = (data.get("message_type") or "chat") == "chat"
	skip = "Not a text message" if not is_text else analysis_skip_reason(data.get("body"))

	doc = frappe.get_doc(
		{
			"doctype": "Periskope Message",
			"message_id": message_id,
			# Only text chats are analysed for now, and tiny ones ('ok', 'done', emoji) are not worth the
			# tokens: both are marked skipped right away and never sent to the AI.
			"classification_status": ("Skipped" if skip else "Pending") if classify else "",
			"classification_error": (skip or "") if classify else "",
			"direction": "Outgoing" if from_me else "Incoming",
			"message_type": data.get("message_type"),
			"message_timestamp": _to_site_datetime(data.get("timestamp")),
			"org_phone": org_phone,
			"rep": _rep_for_phone(org_phone),
			# In a 1-to-1 chat the chat id is the customer's number; groups have no single customer.
			"contact_number": "" if is_group else _digits(chat_id),
			"is_group": 1 if is_group else 0,
			"sender_phone": _digits(data.get("sender_phone")),
			"body": data.get("body"),
			"quoted_message_id": data.get("quoted_message_id"),
			"media_filename": media.get("filename"),
			"media_mimetype": media.get("mimetype"),
			"media_path": media.get("path"),
			"media_size": media.get("size"),
			"chat_id": chat_id,
			"ack": data.get("ack"),
			"performed_by": data.get("performed_by"),
			"webhook_log": webhook_log,
			"raw_payload": json.dumps(data, indent=1),
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		# Periskope can deliver the same event twice at once; the unique key catches it.
		frappe.db.rollback()
		return None

	if classify and not skip:
		frappe.enqueue(
			"periskope_connector.ai.classifier.classify",
			message=doc.name,
			queue="short",
			enqueue_after_commit=True,
		)
	return doc.name


def process_webhook_log(log: str):
	"""Background job: turn one valid webhook log row into a message."""
	row = frappe.db.get_value(
		"Periskope Webhook Log", log, ["event_type", "signature_valid", "payload"], as_dict=True
	)
	if not row or not row.signature_valid or row.event_type != MESSAGE_CREATED:
		return
	try:
		create_message(json.loads(row.payload), webhook_log=log)
	except Exception:
		frappe.log_error(title=f"Periskope: could not save message from webhook log {log}")
		raise


def process_unprocessed_logs():
	"""Catch up on message.created events already logged without a message.
	Run with: bench --site <site> execute periskope_connector.messages.process_unprocessed_logs"""
	logs = frappe.get_all(
		"Periskope Webhook Log",
		filters={"event_type": MESSAGE_CREATED, "signature_valid": 1},
		pluck="name",
		order_by="creation asc",
	)
	for log in logs:
		process_webhook_log(log)
	return len(logs)
