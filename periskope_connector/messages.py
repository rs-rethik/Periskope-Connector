import json
import re

import frappe
import pytz
from dateutil import parser as date_parser
from frappe.utils.data import get_system_timezone

from periskope_connector import attachments, numbers, shared_items
from periskope_connector.autoreply import on_message as autoreply_on_message
from periskope_connector.client import fetch_chat_name
from periskope_connector.contacts import ensure_contact
from periskope_connector.linking import ensure_group, link_message, number_key
from periskope_connector.outbound import match_sent
from periskope_connector.setup_status import agent_on
from periskope_connector.system import insert_as_system
from periskope_connector.text_rules import NOT_ANSWERS, analysis_skip_reason, skip_fields
from periskope_connector.welcome import on_message as welcome_on_message

MESSAGE_CREATED = "message.created"
MESSAGE_UPDATED = "message.updated"
MESSAGE_DELETED = "message.deleted"
MESSAGE_EVENTS = (MESSAGE_CREATED, MESSAGE_UPDATED, MESSAGE_DELETED)
ACK_UPDATED = "message.ack.updated"
REACTION_EVENTS = ("reaction.created", "reaction.updated")
# Every event the webhook saves and processes.
HANDLED_EVENTS = (*MESSAGE_EVENTS, ACK_UPDATED, *REACTION_EVENTS)


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


MEDIA_TYPES = {"image": "image", "video": "video", "document": "document", "audio": "audio", "ptt": "audio"}
# Shared items the inbox shows from the saved payload (shared_items.py).
SHARED_TYPES = {"location": "location", "vcard": "contact", "multi_vcard": "contact", "poll_creation": "poll"}


def normalise_type(raw: str | None) -> str:
	"""Periskope's message type as one of text / image / video / document / audio / location /
	contact / poll / other."""
	raw = (raw or "chat").lower()
	return "text" if raw == "chat" else MEDIA_TYPES.get(raw) or SHARED_TYPES.get(raw, "other")


def _shared_details(kind: str, data: dict) -> str | None:
	"""A location, contact card or poll as the inbox shows it, kept on the message (the raw payload
	is cleared after a while)."""
	found = shared_items.details(kind, data)
	return json.dumps(found) if found else None


def _sales_person_for_phone(number: str) -> str | None:
	"""The Sales Person set on this company number (WhatsApp Number)."""
	return numbers.sales_person(number)


def _rep_for_phone(number: str) -> str | None:
	"""The default rep set on this company number (WhatsApp Number)."""
	return numbers.rep(number)


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
	if from_me:
		from periskope_connector.api.compose import adopt_echo

		if adopt_echo(data, webhook_log):
			return None  # sent from the inbox: the row saved before the send is now filled in
	is_group = chat_id.endswith("@g.us")
	media = data.get("media") or {}
	org_phone = _digits(data.get("org_phone"))

	# A group is saved once, with its name and type, so its messages can be linked to the CRM.
	chat_name = (ensure_group(chat_id, org_phone).group_name or "") if is_group else ""

	classify = agent_on()
	# A text, an image or PDF the AI can read, or an attachment with a caption is analysed; a voice
	# note waits for its transcript.
	kind = normalise_type(data.get("message_type"))
	body = (data.get("body") or "").strip()
	readable_file = attachments.readable(
		frappe._dict(
			message_type=kind,
			media_path=media.get("path"),
			media_mimetype=media.get("mimetype"),
			media_size=media.get("size"),
		)
	)
	has_words = kind == "text" or (kind in ("image", "document", "video") and bool(body))
	if from_me:
		skip = "Sent by the rep"  # only the customer's messages are classified
	elif readable_file and not body:
		skip = None
	else:
		skip = "Not a text message" if not (has_words or readable_file) else analysis_skip_reason(body)

	# Our own assistant's message arrives through the webhook like any other from our number.
	agent_reply = match_sent(chat_id, data) if from_me else ""

	doc = frappe.get_doc(
		{
			"doctype": "Periskope Message",
			"message_id": message_id,
			"agent_reply": agent_reply,
			# Only the customer's messages are analysed, and tiny ones ('ok', 'done', emoji) are not
			# worth the tokens: they are marked skipped right away and never sent to the AI. The rest
			# are analysed at once (ai/classifier.queue; the 5-minute job catches up on any missed).
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
			"whatsapp_number": numbers.ensure(org_phone),
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
			"shared_details": _shared_details(kind, data),
		}
	)
	try:
		insert_as_system(doc)
	except frappe.DuplicateEntryError:
		# Periskope can deliver the same event twice at once; the unique key catches it.
		frappe.db.rollback()
		return None

	if from_me and agent_reply not in NOT_ANSWERS:
		# The rep has answered: the customer's earlier messages in this chat are replied to.
		frappe.db.sql(
			"""update `tabPeriskope Message` set replied = 1
			where chat_id = %s and direction = 'Incoming' and replied = 0 and message_timestamp <= %s""",
			(chat_id, doc.message_timestamp),
		)

	# Linked now by number or group name. A team group's messages are linked again once the AI has
	# found the customer name in the text.
	status = link_message(doc.name)

	# An unknown 1-to-1 number joins the contact queue (guarded: it may never break the saving).
	if status == "No Match":
		ensure_contact(doc)
	if attachments.has_file(doc):
		attachments.queue(doc.name)
	if doc.classification_status == "Pending":
		from periskope_connector.ai import classifier

		classifier.queue(doc.name)
	autoreply_on_message(doc)
	welcome_on_message(doc)
	if not from_me and doc.message_type == "audio":
		from periskope_connector.ai import transcribe

		transcribe.queue(doc.name)

	return doc.name


def process_webhook_log(log: str):
	"""Background job: turn one valid webhook log row into a message."""
	row = frappe.db.get_value(
		"Periskope Webhook Log", log, ["event_type", "signature_valid", "payload"], as_dict=True
	)
	if not row or not row.signature_valid or row.event_type not in HANDLED_EVENTS:
		return
	try:
		payload = json.loads(row.payload)
		if row.event_type == MESSAGE_CREATED:
			create_message(payload, webhook_log=log)
		elif row.event_type == ACK_UPDATED:
			update_ack(payload)
		elif row.event_type in REACTION_EVENTS:
			update_reaction(payload)
		else:
			update_message(payload, webhook_log=log)
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


def update_message(payload: dict, webhook_log: str | None = None) -> str | None:
	"""message.updated / message.deleted: the customer edited or deleted a message, or its delivery
	ticks changed. The stored message follows; an edited
	customer message is tagged again, and a deleted one no longer waits for a reply."""
	data = payload.get("data") or {}
	message_id = data.get("message_id")
	if not message_id:
		return None
	deleted = payload.get("event_type") == MESSAGE_DELETED or bool(data.get("is_deleted"))
	name = frappe.db.get_value("Periskope Message", {"message_id": message_id})
	if not name:
		# An update for a message we never saw (a missed webhook): save it now, unless it is gone.
		return None if deleted else create_message({**payload, "event_type": MESSAGE_CREATED}, webhook_log)
	old = frappe.db.get_value(
		"Periskope Message",
		name,
		["body", "ack", "is_deleted", "direction", "classification_status"],
		as_dict=True,
	)
	values = {}
	if data.get("ack") not in (None, "") and str(data.get("ack")) != str(old.ack or ""):
		values["ack"] = data.get("ack")
	if deleted and not old.is_deleted:
		values.update(is_deleted=1, deleted_on=frappe.utils.now_datetime())
	elif not deleted and data.get("body") is not None and (data.get("body") or "") != (old.body or ""):
		values.update(previous_body=old.body, body=data.get("body"), edited_on=frappe.utils.now_datetime())
		if old.direction == "Incoming" and agent_on():
			values.update(classification_status="Pending", classification_attempts=0)
	if not values:
		return name
	frappe.db.set_value("Periskope Message", name, values, update_modified=False)
	from periskope_connector import chat
	from periskope_connector.linking import CHAT_FIELDS

	# The inbox shows the edit or the deletion at once (its list preview too).
	chat.ensure(frappe.db.get_value("Periskope Message", name, CHAT_FIELDS, as_dict=True))
	if values.get("classification_status") == "Pending":
		from periskope_connector.ai import classifier

		classifier.queue(name)
	return name


MAX_SWEEP_ATTEMPTS = 5  # per webhook log; past this it stays visible in Health as unprocessed


def _find(data: dict) -> str | None:
	"""Our row of the message an event is about: by Periskope's message id, else by the WhatsApp id
	a reply sent from the inbox was given."""
	name = data.get("message_id") and frappe.db.get_value(
		"Periskope Message", {"message_id": data.get("message_id")}
	)
	unique_id = data.get("unique_id")
	if not name and unique_id:
		name = frappe.db.get_value("Periskope Message", {"outbound_id": unique_id})
	return name


def _changed(name: str, kind: str):
	chat_row = frappe.db.get_value("Periskope Message", name, "periskope_chat")
	if chat_row:
		from periskope_connector import inbox_realtime

		inbox_realtime.chat_changed(chat_row, kind=kind)


def update_ack(payload: dict) -> str | None:
	"""message.ack.updated: the delivery ticks of one of our messages. Acks only go up (sent,
	delivered, read); -1 is a message WhatsApp could not send."""
	data = payload.get("data") or {}
	name = _find(data)
	ack = str(data.get("ack") if data.get("ack") is not None else "")
	if not name or not ack:
		return None
	old = frappe.db.get_value("Periskope Message", name, "ack")
	values = {}
	if ack == "-1":
		values = {"ack": ack, "send_status": "Failed", "send_error": "WhatsApp could not send this message."}
	elif not old or old == "-1" or _ack_rank(ack) > _ack_rank(old):
		values = {"ack": ack}
	if not values:
		return name
	frappe.db.set_value("Periskope Message", name, values, update_modified=False)
	_changed(name, "ack")
	return name


def _ack_rank(ack: str | None) -> int:
	try:
		return int(ack)
	except (TypeError, ValueError):
		return -2


def update_reaction(payload: dict) -> str | None:
	"""reaction.created / reaction.updated: who reacted to a message, with what. An empty reaction
	takes it away."""
	data = payload.get("data") or {}
	target = (data.get("msg_id") or {}).get("_serialized") or data.get("message_id")
	name = target and frappe.db.get_value("Periskope Message", {"message_id": target})
	if not name and data.get("unique_id"):
		name = frappe.db.get_value("Periskope Message", {"outbound_id": data.get("unique_id")})
	if not name:
		return None
	who = _digits(data.get("sender_id")) or "unknown"
	reactions = json.loads(frappe.db.get_value("Periskope Message", name, "reactions") or "{}")
	emoji = (data.get("reaction") or "").strip()
	if emoji:
		reactions[who] = emoji
	else:
		reactions.pop(who, None)
	frappe.db.set_value(
		"Periskope Message",
		name,
		"reactions",
		json.dumps(reactions, ensure_ascii=False),
		update_modified=False,
	)
	_changed(name, "reaction")
	return name


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
