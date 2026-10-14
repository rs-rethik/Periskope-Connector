import json
import re

import frappe
import pytz
from dateutil import parser as date_parser
from frappe.utils.data import get_system_timezone

from periskope_connector.autoreply import match_sent
from periskope_connector.autoreply import on_message as autoreply_on_message
from periskope_connector.client import fetch_chat_name
from periskope_connector.contacts import ensure_contact
from periskope_connector.keywords import check_message
from periskope_connector.linking import ensure_group, link_message, number_key
from periskope_connector.system import insert_as_system
from periskope_connector.text_rules import analysis_skip_reason, skip_fields

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


MEDIA_TYPES = {"image": "image", "document": "document", "audio": "audio", "ptt": "audio"}


def normalise_type(raw: str | None) -> str:
	"""Periskope's message type as one of text / image / document / audio / other."""
	raw = (raw or "chat").lower()
	return "text" if raw == "chat" else MEDIA_TYPES.get(raw, "other")


def _sales_person_for_phone(number: str) -> str | None:
	"""The Sales Person assigned to this company number in Periskope Settings."""
	if not number:
		return None
	return frappe.db.get_value(
		"Periskope Phone",
		{"parent": "Periskope Settings", "number": number},
		"sales_person",
	)


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

	# A group is saved once, with its name and type, so its messages can be linked to the CRM.
	chat_name = (ensure_group(chat_id, org_phone).group_name or "") if is_group else ""

	classify = bool(frappe.db.get_single_value("Periskope Settings", "enable_classification"))
	is_text = (data.get("message_type") or "chat") == "chat"
	if from_me:
		skip = "Sent by the rep"  # only the customer's messages are classified
	else:
		skip = "Not a text message" if not is_text else analysis_skip_reason(data.get("body"))

	# Our own assistant's message arrives through the webhook like any other from our number.
	agent_reply = match_sent(chat_id, data.get("body")) if from_me else ""

	doc = frappe.get_doc(
		{
			"doctype": "Periskope Message",
			"message_id": message_id,
			"agent_reply": agent_reply,
			# Only the customer's text chats are analysed, and tiny ones ('ok', 'done', emoji) are not
			# worth the tokens: they are marked skipped right away and never sent to the AI. The rest
			# wait for the scheduled job (ai/classifier.py, every 5 minutes).
			**(
				skip_fields(skip)
				if (classify and skip)
				else {"classification_status": "Pending" if classify else ""}
			),
			"direction": "Outgoing" if from_me else "Incoming",
			"message_type": normalise_type(data.get("message_type")),
			"periskope_type": data.get("message_type"),
			"message_timestamp": _to_site_datetime(data.get("timestamp")),
			"org_phone": org_phone,
			"rep": _rep_for_phone(org_phone),
			"sales_person": _sales_person_for_phone(org_phone),
			# In a 1-to-1 chat the chat id is the customer's number; groups have no single customer.
			"contact_number": "" if is_group else _digits(chat_id),
			"customer_phone": number_key(data.get("sender_phone") if is_group else chat_id),
			# In a 1-to-1 chat the Periskope chat name is the contact's WhatsApp name. The webhook
			# carries no sender name, so a group message has none.
			"sender_name": "" if (is_group or from_me) else fetch_chat_name(chat_id, org_phone),
			"is_group": 1 if is_group else 0,
			"sender_phone": _digits(data.get("sender_phone")),
			"body": data.get("body"),
			"quoted_message_id": data.get("quoted_message_id"),
			"media_filename": media.get("filename"),
			"media_mimetype": media.get("mimetype"),
			"media_path": media.get("path"),
			"media_size": media.get("size"),
			"chat_id": chat_id,
			"chat_name": chat_name,
			"ack": data.get("ack"),
			"performed_by": data.get("performed_by"),
			"webhook_log": webhook_log,
			"raw_payload": json.dumps(data, indent=1),
		}
	)
	try:
		insert_as_system(doc)
	except frappe.DuplicateEntryError:
		# Periskope can deliver the same event twice at once; the unique key catches it.
		frappe.db.rollback()
		return None

	if from_me and agent_reply != "Holding":
		# The rep has answered: the customer's earlier messages in this chat are replied to.
		frappe.db.sql(
			"""update `tabPeriskope Message` set replied = 1
			where chat_id = %s and direction = 'Incoming' and replied = 0 and message_timestamp <= %s""",
			(chat_id, doc.message_timestamp),
		)

	# Linked now by number or group name. A team group's messages are linked again once the AI has
	# found the customer name in the text.
	status = link_message(doc.name)

	# An unknown 1-to-1 number joins the contact queue, and the keyword rules run over the text.
	# Both are guarded: neither may ever break the saving of a message.
	if status == "No Match":
		ensure_contact(doc)
	if not from_me and (doc.body or "").strip():
		check_message(doc.name)
	autoreply_on_message(doc)

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
		# Count the attempt, so the dead-letter sweep below stops re-trying a poison payload.
		try:
			attempts = (frappe.db.get_value("Periskope Webhook Log", log, "attempts") or 0) + 1
			frappe.db.set_value("Periskope Webhook Log", log, "attempts", attempts)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
		frappe.log_error(title=f"Periskope: could not save message from webhook log {log}")
		raise


MAX_SWEEP_ATTEMPTS = 5  # per webhook log; past this it stays visible in Health as unprocessed


def sweep_unprocessed():
	"""Scheduler, every 30 minutes: re-queue message.created logs whose background job never ran
	(a worker restart, a crash). Duplicate deliveries are still safe: create_message is
	idempotent on message_id."""
	logs = frappe.db.sql(
		"""select l.name from `tabPeriskope Webhook Log` l
		left join `tabPeriskope Message` m on m.webhook_log = l.name
		where l.event_type = 'message.created' and l.signature_valid = 1
			and m.name is null
			and ifnull(l.attempts, 0) < %s
			and l.creation < now() - interval 10 minute
			and not exists (
				select 1 from `tabPeriskope Message` pm
				where pm.message_id = json_unquote(json_extract(l.payload, '$.data.message_id'))
			)
		order by l.creation asc
		limit 100""",
		(MAX_SWEEP_ATTEMPTS,),
		as_list=True,
	)
	for (log,) in logs:
		frappe.enqueue("periskope_connector.messages.process_webhook_log", log=log, queue="short")
	return len(logs)


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
