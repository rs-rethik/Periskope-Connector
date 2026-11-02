"""Writing from the WhatsApp inbox: replies (text, a file, a reply to a message), new chats, quick
replies and private notes.

A reply always goes out from the chat's own number (the chat row is per number, chat.py). It is saved
first as an Outgoing Periskope Message with send status "Sending" and committed, then handed to
Periskope through outbound.send, which keeps the test-mode guard. Periskope's webhook copy of the
message then fills in that same row (adopt_echo), however fast it arrives. A send Periskope refuses is
"Failed" with its error, and the user can retry it; one that never reached Periskope is retried
by itself (retry_failed). Notes are never sent to WhatsApp.
"""

import base64
import json
import re

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

from periskope_connector import access, attachments, chat, meta, safety, shared_items
from periskope_connector.outbound import send as send_whatsapp
from periskope_connector.setup_status import agent_on
from periskope_connector.text_rules import skip_fields

MAX_FILE_BYTES = attachments.MAX_BYTES
MAX_TEXT = 4096
AUTO_RETRIES = 3
# Errors where Periskope certainly did not take the message, so sending again cannot double it.
RETRY_SAFE = re.compile(r"ConnectionError|ConnectTimeout|HTTP (429|502|503)\b")
CHAT_FIELDS = [
	"name",
	"chat_id",
	"org_phone",
	"whatsapp_number",
	"contact_number",
	"is_group",
	"chat_name",
	"crm_lead",
	"crm_deal",
]


def _chat(chat_name: str) -> frappe._dict:
	access.require_inbox_user()
	if not access.can_see_chat(chat_name):
		frappe.throw(_("You cannot reply in this chat."), frappe.PermissionError)
	row = frappe.db.get_value("Periskope Chat", chat_name, CHAT_FIELDS, as_dict=True)
	if not row.whatsapp_number and not row.org_phone:
		frappe.throw(_("This chat has no company number to reply from."))
	return row


def media_type(mimetype: str) -> str:
	"""Periskope's media type for a file."""
	mimetype = (mimetype or "").lower()
	for prefix in ("image", "video", "audio"):
		if mimetype.startswith(prefix + "/"):
			return prefix
	return "document"


def _own_file(file_name: str) -> frappe._dict:
	"""A file the user just uploaded for this reply: private, theirs and small enough."""
	f = frappe.db.get_value(
		"File", file_name, ["name", "owner", "is_private", "file_name", "file_size"], as_dict=True
	)
	if not f or (f.owner != frappe.session.user and not access.is_manager()):
		frappe.throw(_("That file cannot be sent."), frappe.PermissionError)
	if (f.file_size or 0) > MAX_FILE_BYTES:
		frappe.throw(_("The file is larger than {0} MB.").format(MAX_FILE_BYTES // (1024 * 1024)))
	return f


@frappe.whitelist(methods=["POST"])
def send(chat: str, text: str = "", reply_to: str | None = None, file: str | None = None) -> dict:
	"""Reply in a chat from its own number. Returns the saved message (with its send status)."""
	access.require_inbox_user()
	row = _chat(chat)
	text = (text or "").strip()
	if not text and not file:
		frappe.throw(_("Type a message or attach a file."))
	if len(text) > MAX_TEXT:
		frappe.throw(_("The message is longer than {0} characters.").format(MAX_TEXT))
	quoted = None
	if reply_to:
		quoted = frappe.db.get_value(
			"Periskope Message", reply_to, ["message_id", "periskope_chat"], as_dict=True
		)
		if not quoted or quoted.periskope_chat != chat or (quoted.message_id or "").startswith("local_"):
			frappe.throw(_("You can only reply to a delivered message of this chat."))
	upload = _own_file(file) if file else None
	name = _save_outgoing(row, text, quoted.message_id if quoted else None, upload)
	_deliver(name)
	return _row(name)


def _save_outgoing(row, text: str, reply_to: str | None, upload) -> str:
	mimetype = ""
	kind = "text"
	if upload:
		import mimetypes

		mimetype = mimetypes.guess_type(upload.file_name or "")[0] or "application/octet-stream"
		kind = media_type(mimetype)
	doc = frappe.get_doc(
		{
			"doctype": "Periskope Message",
			"message_id": f"local_{frappe.generate_hash(length=20)}",
			"direction": "Outgoing",
			"message_type": kind,
			"body": text,
			"message_timestamp": now_datetime(),
			"chat_id": row.chat_id,
			"chat_name": row.chat_name if row.is_group else "",
			"org_phone": row.org_phone or row.whatsapp_number,
			"whatsapp_number": row.whatsapp_number,
			"periskope_chat": row.name,
			"contact_number": row.contact_number,
			"is_group": row.is_group,
			"crm_lead": row.crm_lead,
			"crm_deal": row.crm_deal,
			"sent_by": frappe.session.user,
			"send_status": "Sending",
			"send_attempts": 0,
			"reply_to": reply_to,
			"quoted_message_id": _short_id(reply_to),
			"media_filename": upload.file_name if upload else None,
			"media_mimetype": mimetype or None,
			"media_size": upload.file_size if upload else None,
			"attachment_file": upload.name if upload else None,
			# Our own reply is not analysed by the AI (as for a reply sent from the phone).
			**(skip_fields("Sent by the rep") if agent_on() else {}),
		}
	)
	doc.insert(ignore_permissions=True)  # the caller's access to the chat was checked in _chat
	if upload:
		frappe.db.set_value(
			"File", upload.name, {"attached_to_doctype": "Periskope Message", "attached_to_name": doc.name}
		)
	# Committed before Periskope is called: its webhook copy may arrive within a second and must
	# find this row (adopt_echo), not be saved as a second message.
	frappe.db.commit()
	return doc.name


def _short_id(message_id: str | None) -> str | None:
	"""The WhatsApp id inside Periskope's message id ("true_<chat>_<id>[_<participant>]") or a Meta
	message's id ("meta_<id>")."""
	if (message_id or "").startswith(meta.PREFIX):
		return message_id[len(meta.PREFIX) :]
	parts = (message_id or "").split("_")
	return parts[2] if len(parts) >= 3 else None


def _deliver(name: str) -> str:
	"""Hand one saved Outgoing message to Periskope. Returns its send status."""
	m = frappe.db.get_value(
		"Periskope Message",
		name,
		[
			"name",
			"chat_id",
			"body",
			"org_phone",
			"reply_to",
			"attachment_file",
			"media_filename",
			"media_mimetype",
			"sent_by",
			"send_attempts",
			"periskope_chat",
		],
		as_dict=True,
	)
	media = None
	if m.attachment_file:
		media = {"file": m.attachment_file, "mimetype": m.media_mimetype, "filename": m.media_filename}
		if not meta.is_meta(m.org_phone):  # Periskope takes the file itself; Meta takes a link
			media.update(
				type=media_type(m.media_mimetype),
				filedata=base64.b64encode(attachments._read(m.attachment_file)).decode(),
			)
	attempts = cint(m.send_attempts) + 1
	try:
		result = send_whatsapp(
			m.chat_id,
			m.body or "",
			m.org_phone,
			origin="Inbox",
			media=media,
			reply_to=m.reply_to,
			detail={"user": m.sent_by, "message": name},
		)
	except safety.SendNotAllowed:
		_set(
			name,
			send_status="Not Sent",
			send_attempts=attempts,
			send_error=_("Test mode: messages go only to the test numbers."),
		)
		return "Not Sent"
	except Exception as e:
		_set(name, send_status="Failed", send_attempts=attempts, send_error=str(e)[:500])
		return "Failed"
	_set(
		name,
		send_status="Queued",
		send_attempts=attempts,
		send_error="",
		outbound_id=(result or {}).get("unique_id") or None,
		ack="0",
	)
	_after_rep_reply(name)
	return "Queued"


def _set(name: str, **values):
	frappe.db.set_value("Periskope Message", name, values, update_modified=True)
	frappe.db.commit()
	chat_name = frappe.db.get_value("Periskope Message", name, "periskope_chat")
	if chat_name:
		from periskope_connector import inbox_realtime

		inbox_realtime.chat_changed(chat_name, kind="send")


def _after_rep_reply(name: str):
	"""The rep answered from the inbox: the same as a reply from their phone. The waiting AI reply
	is cancelled, the customer's earlier messages count as replied, and the chat and its CRM link
	are brought up to date."""
	from periskope_connector import autoreply
	from periskope_connector.linking import link_message

	m = frappe.db.get_value("Periskope Message", name, ["chat_id", "message_timestamp"], as_dict=True)
	autoreply.cancel(m.chat_id, _("A rep replied"))
	frappe.db.sql(
		"""update `tabPeriskope Message` set replied = 1
		where chat_id = %s and direction = 'Incoming' and replied = 0 and message_timestamp <= %s""",
		(m.chat_id, m.message_timestamp),
	)
	link_message(name)
	frappe.db.commit()


def adopt_echo(data: dict, webhook_log: str | None = None) -> str | None:
	"""Periskope's copy of a message sent from the inbox: fill in the row saved before the send
	instead of adding a second message. Returns the row's name, or None when it is not one of ours."""
	unique_id = data.get("unique_id") or _short_id(data.get("message_id"))
	name = unique_id and frappe.db.get_value("Periskope Message", {"outbound_id": unique_id})
	if not name:
		# The copy can come before Periskope's answer to the send was saved: the newest row still
		# sending in that chat, with the same text.
		number = re.sub(r"\D", "", (data.get("org_phone") or "").split("@")[0])
		name = frappe.db.get_value(
			"Periskope Message",
			{
				"chat_id": data.get("chat_id"),
				"org_phone": number,
				"send_status": "Sending",
				"body": (data.get("body") or "").strip(),
				"creation": [">=", add_to_date(now_datetime(), minutes=-10)],
			},
			order_by="creation desc",
		)
	if not name:
		return None
	values = {
		"message_id": data.get("message_id"),
		"ack": data.get("ack"),
		"raw_payload": json.dumps(data, indent=1),
		"webhook_log": webhook_log,
		"outbound_id": unique_id,
	}
	if (frappe.db.get_value("Periskope Message", name, "send_status") or "") in ("Sending", "Failed"):
		values["send_status"] = "Queued"
		values["send_error"] = ""
	media = data.get("media") or {}
	if media.get("path"):
		values["media_path"] = media.get("path")
	frappe.db.set_value("Periskope Message", name, {k: v for k, v in values.items() if v is not None})
	from periskope_connector.linking import link_message

	link_message(name)
	return name


@frappe.whitelist(methods=["POST"])
def retry(message: str) -> dict:
	"""Send a failed reply again."""
	access.require_inbox_user()
	m = frappe.db.get_value(
		"Periskope Message", message, ["periskope_chat", "send_status", "sent_by"], as_dict=True
	)
	if not m or not access.can_see_chat(m.periskope_chat):
		frappe.throw(_("You cannot retry this message."), frappe.PermissionError)
	if m.send_status != "Failed":
		frappe.throw(_("Only a failed message can be sent again."))
	_set(message, send_status="Sending")
	_deliver(message)
	return _row(message)


def retry_failed() -> int:
	"""Scheduler, every minute: send again the replies that never reached Periskope (no connection,
	busy, too many requests), up to AUTO_RETRIES times. Others wait for the user's Retry."""
	rows = frappe.get_all(
		"Periskope Message",
		filters={
			"send_status": "Failed",
			"send_attempts": ["<", AUTO_RETRIES],
			"modified": ["<", add_to_date(now_datetime(), minutes=-1)],
		},
		fields=["name", "send_error"],
		limit=20,
	)
	sent = 0
	for row in rows:
		if not RETRY_SAFE.search(row.send_error or ""):
			continue
		try:
			if _deliver(row.name) == "Queued":
				sent += 1
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Periskope: retry of message {row.name} failed")
	return sent


TICK_LOOKUPS = 30  # per run


TICK_PAUSE_SECONDS = 10  # one chat's ticks are asked for at most this often


def refresh_ticks(chat: str | None = None) -> int:
	"""Ask Periskope how far the inbox's recent replies got (sent, delivered, read), for sites whose
	webhook does not reach them (no public address, or the ack event not subscribed). Webhook ack
	events do the same at once where they arrive. Runs from the scheduler every 5 minutes, and for
	one chat when the inbox opens it or has just sent in it (refresh_chat_ticks)."""
	from periskope_connector import health, messages
	from periskope_connector.client import request

	filters = {
		"send_status": "Queued",
		"outbound_id": ["is", "set"],
		"ack": ["not in", ["4", "5"]],
		"creation": [">", add_to_date(now_datetime(), days=-2)],
	}
	# Meta's ticks come through frappe_whatsapp (meta.on_update), not from Periskope
	meta_numbers = frappe.get_all("WhatsApp Number", filters={"source": meta.SOURCE}, pluck="name")
	if meta_numbers:
		filters["whatsapp_number"] = ["not in", meta_numbers]
	if chat:
		filters["periskope_chat"] = chat
	rows = frappe.get_all(
		"Periskope Message",
		filters=filters,
		fields=["name", "outbound_id", "org_phone", "message_id"],
		order_by="creation desc",
		limit=TICK_LOOKUPS,
	)
	updated = 0
	for row in rows:
		try:
			status = request("GET", f"message/{row.outbound_id}/status", phone=row.org_phone) or {}
		except Exception:
			health.log_once("Periskope: could not read the delivery status of inbox replies")
			break
		if status.get("message_id") and (row.message_id or "").startswith("local_"):
			frappe.db.set_value("Periskope Message", row.name, "message_id", status["message_id"])
		if status.get("ack") is not None:
			messages.update_ack({"data": {"unique_id": row.outbound_id, "ack": str(status["ack"])}})
			updated += 1
	frappe.db.commit()
	return updated


@frappe.whitelist(methods=["POST"])
def refresh_chat_ticks(chat: str) -> int:
	"""The inbox asks for a chat's ticks when it opens it and shortly after a send. At most once every
	TICK_PAUSE_SECONDS per chat, whoever asks; the ticks then arrive like any live update."""
	access.require_inbox_user()
	if not access.can_see_chat(chat):
		frappe.throw(_("You cannot open this chat."), frappe.PermissionError)
	key = f"periskope_ticks:{chat}"
	if frappe.cache().get_value(key, expires=True):
		return 0
	frappe.cache().set_value(key, 1, expires_in_sec=TICK_PAUSE_SECONDS)
	return refresh_ticks(chat)


def _row(name: str) -> dict:
	from periskope_connector.api.inbox import MESSAGE_FIELDS

	row = frappe.db.get_value("Periskope Message", name, MESSAGE_FIELDS, as_dict=True)
	row.has_media = bool(row.attachment_file) or attachments.has_file(row)
	row.pop("media_path", None)
	row.shared = shared_items.from_row(row)
	return row


# --- Meta WhatsApp templates ---------------------------------------------------------------------


def _meta_chat(chat: str) -> frappe._dict:
	row = _chat(chat)
	if not meta.is_meta(row.whatsapp_number or row.org_phone):
		frappe.throw(_("Templates are sent only in a Meta WhatsApp chat."))
	return row


@frappe.whitelist()
def meta_templates(chat: str) -> list[dict]:
	"""The approved templates a Meta WhatsApp chat can be sent (any time, also after 24 hours)."""
	access.require_inbox_user()
	_meta_chat(chat)
	return meta.templates()


@frappe.whitelist(methods=["POST"])
def send_template(chat: str, template: str, params: str | list | None = None) -> dict:
	"""Send an approved template in a Meta WhatsApp chat, with a value for each {{n}} of its body.
	Returns the saved message."""
	access.require_inbox_user()
	row = _meta_chat(chat)
	found = next((t for t in meta.templates() if t["name"] == template), None)
	if not found:
		frappe.throw(_("Choose one of the approved templates."))
	params = [
		str(p or "").strip() for p in (frappe.parse_json(params) if isinstance(params, str) else params or [])
	]
	if len(params) < found["params"] or not all(params[: found["params"]]):
		frappe.throw(_("Fill in every value of the template."))
	text = meta.template_text(template, frappe.as_json(params[: found["params"]]))
	try:
		result = send_whatsapp(
			row.chat_id,
			text,
			row.whatsapp_number or row.org_phone,
			origin="Inbox",
			template=template,
			params=params[: found["params"]],
			detail={"user": frappe.session.user, "template": template},
		)
	except safety.SendNotAllowed:
		frappe.throw(_("Test mode: messages go only to the test numbers."))
	# Saved here at once (the WhatsApp Message hook would also do it, a moment later).
	name = meta.ingest(result["whatsapp_message"])
	if not name:
		frappe.throw(_("The template was sent, but could not be shown here yet. Reload the chat."))
	frappe.db.set_value("Periskope Message", name, "sent_by", frappe.session.user, update_modified=False)
	_after_rep_reply(name)
	return _row(name)


# --- New chat ------------------------------------------------------------------------------------


@frappe.whitelist(methods=["POST"])
def start_chat(number: str, phone: str, text: str) -> dict:
	"""Write first to a customer: from one of the user's numbers, to a 1-to-1 WhatsApp number.
	The chat is assigned to the user who started it."""
	access.require_inbox_user()
	allowed = access.allowed_numbers()
	if not frappe.db.get_value("WhatsApp Number", number, "is_active") or (
		allowed is not None and number not in allowed
	):
		frappe.throw(_("You cannot send from that number."), frappe.PermissionError)
	digits = re.sub(r"\D", "", phone or "")
	if len(digits) < 10:
		frappe.throw(_("Enter the customer's WhatsApp number with the country code."))
	if len(digits) == 10:
		digits = "91" + digits  # a local Indian number
	chat_id = f"{digits}@c.us"
	name = chat.name_of(number, chat_id)
	if not name:
		name = (
			frappe.get_doc(
				{
					"doctype": "Periskope Chat",
					"chat_id": chat_id,
					"contact_number": digits,
					"org_phone": number,
					"whatsapp_number": number,
					"status": "Open",
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
		# The user who starts the chat owns it (so they see it, access.py); then its team and clocks.
		from periskope_connector import workflow

		workflow.assign(name, [frappe.session.user], by=frappe.session.user)
		workflow.new_chat(name)
	elif not access.can_see_chat(name):
		from periskope_connector import workflow

		workflow.assign(name, [frappe.session.user], by=frappe.session.user)
	frappe.db.commit()
	result = send(name, text)
	return {"chat": name, "message": result}


# --- Quick replies -------------------------------------------------------------------------------


@frappe.whitelist()
def canned_responses() -> list[dict]:
	"""The user's own quick replies and the shared ones."""
	access.require_inbox_user()
	return frappe.get_all(
		"Periskope Canned Response",
		or_filters={"owner": frappe.session.user, "is_shared": 1},
		fields=["name", "title", "body", "is_shared", "owner"],
		order_by="title asc",
		limit=500,
	)


@frappe.whitelist(methods=["POST"])
def save_canned_response(title: str, body: str, is_shared: int = 0, name: str | None = None) -> dict:
	"""Add or change a quick reply. Everyone keeps their own; managers can share one with all."""
	access.require_inbox_user()
	shared = 1 if cint(is_shared) and access.is_manager() else 0
	if name:
		doc = frappe.get_doc("Periskope Canned Response", name)
		if doc.owner != frappe.session.user and not access.is_manager():
			frappe.throw(_("You can only change your own quick replies."), frappe.PermissionError)
		doc.update({"title": title, "body": body, "is_shared": shared})
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc(
			{"doctype": "Periskope Canned Response", "title": title, "body": body, "is_shared": shared}
		).insert(ignore_permissions=True)
	return {"name": doc.name, "title": doc.title, "body": doc.body, "is_shared": doc.is_shared}


@frappe.whitelist(methods=["POST"])
def delete_canned_response(name: str) -> None:
	access.require_inbox_user()
	owner = frappe.db.get_value("Periskope Canned Response", name, "owner")
	if owner != frappe.session.user and not access.is_manager():
		frappe.throw(_("You can only delete your own quick replies."), frappe.PermissionError)
	frappe.delete_doc("Periskope Canned Response", name, ignore_permissions=True)


# --- Private notes -------------------------------------------------------------------------------


@frappe.whitelist()
def mentionable(chat: str) -> list[dict]:
	"""The teammates who can be @mentioned in a note: whoever may work this chat's number."""
	access.require_inbox_user()
	_chat(chat)
	from periskope_connector.inbox_realtime import _recipients

	number = frappe.db.get_value("Periskope Chat", chat, "whatsapp_number")
	users = _recipients(number)
	return frappe.get_all(
		"User",
		filters={"name": ["in", users or [""]], "user_type": "System User"},
		fields=["name", "full_name", "user_image"],
		order_by="full_name asc",
	)


@frappe.whitelist(methods=["POST"])
def add_comment(chat: str, content: str, mentions: str | list | None = None) -> dict:
	"""A private note on the chat for the team. Never sent to WhatsApp. Mentioned teammates get a
	notification."""
	access.require_inbox_user()
	_chat(chat)
	content = (content or "").strip()
	if not content:
		frappe.throw(_("Write the note first."))
	if isinstance(mentions, str):
		mentions = json.loads(mentions or "[]")
	allowed = {u.name for u in mentionable(chat)}
	mentions = [u for u in (mentions or []) if u in allowed and u != frappe.session.user]
	doc = frappe.get_doc(
		{
			"doctype": "Periskope Chat Comment",
			"chat": chat,
			"content": content,
			"mentions": "\n".join(mentions),
		}
	).insert(ignore_permissions=True)
	if mentions:
		from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

		title = frappe.db.get_value("Periskope Chat", chat, "chat_name") or chat
		enqueue_create_notification(
			mentions,
			{
				"type": "Mention",
				"document_type": "Periskope Chat",
				"document_name": chat,
				"subject": _("{0} mentioned you in a WhatsApp note on {1}").format(
					frappe.utils.get_fullname(frappe.session.user), title
				),
				"email_content": frappe.utils.escape_html(content),
				"from_user": frappe.session.user,
			},
		)
	from periskope_connector import inbox_realtime

	inbox_realtime.chat_changed(chat, kind="note")
	return comment_row(doc)


def comment_row(doc) -> dict:
	return {
		"name": doc.name,
		"kind": "note",
		"content": doc.content,
		"owner": doc.owner,
		"owner_name": frappe.utils.get_fullname(doc.owner),
		"creation": doc.creation,
		"mentions": [m for m in (doc.mentions or "").split("\n") if m],
	}
