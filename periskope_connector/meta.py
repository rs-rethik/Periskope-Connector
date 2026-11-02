"""Meta WhatsApp in the WhatsApp app: the chats of one WhatsApp Account of the frappe_whatsapp app,
beside the Periskope numbers (Periskope Settings > Meta WhatsApp).

frappe_whatsapp keeps its own messages (WhatsApp Message) and is the only one that talks to Meta. This
app reads them and never changes them:

- Each message of the connected account becomes a Periskope Message on the account's number (a
  WhatsApp Number with source "Meta"), through the same path as a Periskope message
  (messages.create_message), so chats, CRM links, AI tags, auto replies, SLA and reports work alike.
  The copy keeps the WhatsApp Message name (meta_message) and its id is "meta_<WhatsApp id>".
- New messages come from WhatsApp Message's after_insert (a background job after the commit); delivery
  ticks and files that arrive later come from its on_update.
- A reply from this app (a rep's, the AI's or the welcome) is a new Outgoing WhatsApp Message, which
  frappe_whatsapp sends to Meta. Meta takes free text only within 24 hours of the customer's last
  message; after that only an approved template (send_template).
- import_history reads the account's earlier messages once, without the AI, auto replies, alerts or
  SLA clocks.
"""

import json
import mimetypes
import re

import frappe
import pytz
from frappe import _
from frappe.utils import add_to_date, get_datetime, get_url, now_datetime
from frappe.utils.data import get_system_timezone

from periskope_connector import numbers, safety
from periskope_connector.periskope_connector.doctype.whatsapp_number.whatsapp_number import digits

SOURCE = "Meta"
PREFIX = "meta_"  # our message id for a Meta message: "meta_<WhatsApp id>"
WINDOW_HOURS = 24  # Meta takes free text only this long after the customer's last message
IMPORT_BATCH = 500
FIELDS = [
	"name",
	"creation",
	"type",
	"from",
	"to",
	"profile_name",
	"message",
	"message_id",
	"message_type",
	"use_template",
	"template",
	"template_parameters",
	"content_type",
	"attach",
	"is_reply",
	"reply_to_message_id",
	"status",
	"whatsapp_account",
]
# frappe_whatsapp's content type -> the Periskope message type create_message understands
RAW_TYPES = {
	"text": "chat",
	"image": "image",
	"sticker": "image",
	"video": "video",
	"audio": "audio",
	"document": "document",
	"location": "location",
	"contacts": "vcard",
}
MEDIA_KINDS = {"image", "video", "audio", "document"}
# frappe_whatsapp's status -> Periskope's ack (1 sent, 3 delivered, 4 read, -1 failed)
ACKS = {"success": "1", "sent": "1", "delivered": "3", "read": "4", "failed": "-1"}


class WindowClosed(frappe.ValidationError):
	pass


# --- Settings ----------------------------------------------------------------------------------


def installed() -> bool:
	return bool(frappe.db.exists("DocType", "WhatsApp Message"))


def account() -> str | None:
	"""The connected WhatsApp Account, or None when Meta WhatsApp is off."""
	s = frappe.get_cached_doc("Periskope Settings")
	if not (s.get("meta_enabled") and s.get("meta_account") and installed()):
		return None
	return s.meta_account


def number() -> str:
	return digits(frappe.get_cached_doc("Periskope Settings").get("meta_number"))


def is_meta(phone: str | None) -> bool:
	"""True for the number of a Meta WhatsApp Account (its chats are sent through frappe_whatsapp)."""
	return bool(phone) and frappe.db.get_value("WhatsApp Number", digits(phone), "source") == SOURCE


def account_of(phone: str | None) -> str | None:
	return frappe.db.get_value("WhatsApp Number", digits(phone), "whatsapp_account") if phone else None


def connect() -> dict:
	"""Read the connected account's number and name from Meta and add it as a WhatsApp Number.
	Run from Settings > Meta WhatsApp > Connect."""
	from frappe.integrations.utils import make_get_request

	if not installed():
		frappe.throw(_("Install the frappe_whatsapp app first."))
	settings = frappe.get_single("Periskope Settings")
	name = settings.get("meta_account")
	if not name or not frappe.db.exists("WhatsApp Account", name):
		frappe.throw(_("Choose the WhatsApp Account to connect."))
	acc = frappe.get_doc("WhatsApp Account", name)
	if acc.status != "Active":
		frappe.throw(_("The WhatsApp Account {0} is not active.").format(name))
	try:
		info = make_get_request(
			f"{acc.url}/{acc.version}/{acc.phone_id}?fields=display_phone_number,verified_name",
			headers={"authorization": f"Bearer {acc.get_password('token')}"},
		)
	except Exception:
		frappe.log_error(title="Periskope: could not read the Meta WhatsApp number")
		frappe.throw(_("Meta did not answer for this account. Check its token and phone id."))
	phone = digits(info.get("display_phone_number"))
	if not phone:
		frappe.throw(_("Meta gave no number for this account."))
	frappe.db.set_single_value(
		"Periskope Settings",
		{"meta_number": phone, "meta_name": info.get("verified_name") or "", "meta_enabled": 1},
	)
	numbers.ensure(phone)
	row = frappe.db.get_value("WhatsApp Number", phone, ["display_name"], as_dict=True)
	values = {
		"source": SOURCE,
		"whatsapp_account": name,
		"is_ready": 1,
		"wa_state": "CONNECTED",
		"phone_name": info.get("verified_name") or "",
		"last_sync_on": now_datetime(),
	}
	if not row.display_name or row.display_name == phone:
		values["display_name"] = info.get("verified_name") or name
	frappe.db.set_value("WhatsApp Number", phone, values)
	frappe.clear_document_cache("Periskope Settings", "Periskope Settings")
	return {"number": phone, "name": info.get("verified_name") or ""}


# --- Reading messages --------------------------------------------------------------------------


def on_insert(doc, method=None):
	"""WhatsApp Message after_insert: copy a message of the connected account (in the background,
	after frappe_whatsapp's own save is committed). Never raises."""
	try:
		if _ours(doc):
			frappe.enqueue(
				"periskope_connector.meta.ingest",
				queue="short",
				enqueue_after_commit=True,
				message=doc.name,
			)
	except Exception:
		frappe.log_error(title="Periskope: could not queue a Meta WhatsApp message")


def on_update(doc, method=None):
	"""WhatsApp Message on_update: delivery ticks, and a received file saved after the message.
	Never raises."""
	try:
		if not _ours(doc) or doc.flags.in_insert:
			return
		name = frappe.db.get_value("Periskope Message", {"meta_message": doc.name})
		if not name:
			return
		values = {}
		ack = ACKS.get((doc.status or "").lower())
		if ack:
			values["ack"] = ack
			if ack == "-1":
				values.update(send_status="Failed", send_error=_("Meta could not deliver this message."))
		values.update(_media(doc))
		if values:
			frappe.db.set_value("Periskope Message", name, values, update_modified=False)
			chat = frappe.db.get_value("Periskope Message", name, "periskope_chat")
			if chat:
				from periskope_connector import inbox_realtime

				inbox_realtime.chat_changed(chat, kind="ack")
	except Exception:
		frappe.log_error(title="Periskope: could not update a Meta WhatsApp message")


def _ours(doc) -> bool:
	acc = account()
	return bool(acc) and doc.whatsapp_account == acc and (doc.content_type or "") != "reaction"


def ingest(message: str, history: bool = False) -> str | None:
	"""Copy one WhatsApp Message into the WhatsApp app (create_message). Returns the Periskope Message
	name; a message copied before is not copied again."""
	from periskope_connector.messages import create_message

	wm = frappe.db.get_value("WhatsApp Message", message, FIELDS, as_dict=True)
	acc = account()
	if not wm or not acc or wm.whatsapp_account != acc or wm.content_type == "reaction":
		return None
	message_id = PREFIX + (wm.message_id or wm.name)
	existing = frappe.db.get_value("Periskope Message", {"message_id": message_id})
	if not existing:
		data = payload(wm)
		if not data:
			return None
		frappe.flags.periskope_history = history
		try:
			create_message({"event": "message.created", "data": data})
		finally:
			frappe.flags.periskope_history = False
		existing = frappe.db.get_value("Periskope Message", {"message_id": message_id})
	if existing:
		values = {"meta_message": wm.name, **_media(wm)}
		frappe.db.set_value("Periskope Message", existing, values, update_modified=False)
	return existing


def payload(wm) -> dict | None:
	"""A WhatsApp Message as the data of a Periskope message.created webhook."""
	incoming = wm.type == "Incoming"
	customer = phone_number(wm.get("from") if incoming else wm.to)
	ours = number()
	if not customer or not ours:
		return None
	kind = RAW_TYPES.get(wm.content_type or "text", "chat")
	body = wm.message or ""
	if (wm.message_type == "Template" or wm.use_template or wm.template) and (
		wm.template_parameters or not body or body.startswith("{")
	):
		# the template as the customer read it (other senders keep the request itself as the text)
		body = template_text(wm.template, wm.template_parameters) or body
	media = {}
	if kind in ("image", "video", "audio", "document") and wm.attach:
		filename = wm.attach.rsplit("/", 1)[-1]
		media = {
			"path": wm.attach if wm.attach.startswith("http") else get_url(wm.attach),
			"filename": filename,
			"mimetype": mimetypes.guess_type(filename)[0] or "",
		}
	stamp = pytz.timezone(get_system_timezone()).localize(get_datetime(wm.creation))
	return {
		"message_id": PREFIX + (wm.message_id or wm.name),
		# a reply sent from this app is found by Meta's id (compose.adopt_echo, outbound.match_sent)
		"unique_id": wm.message_id or "",
		"chat_id": f"{customer}@c.us",
		"from_me": not incoming,
		"org_phone": ours,
		"message_type": kind,
		"body": body,
		"timestamp": stamp.isoformat(),
		"media": media,
		"quoted_message_id": wm.reply_to_message_id if wm.is_reply else None,
		"sender_phone": customer if incoming else ours,
		"sender_name": (wm.profile_name or "") if incoming else "",
		"ack": "" if incoming else ACKS.get((wm.status or "").lower(), "1"),
	}


def _media(wm) -> dict:
	"""The received or sent file of a WhatsApp Message: its own File, which the inbox serves."""
	if not wm.attach or (wm.content_type or "") not in MEDIA_KINDS:
		return {}
	file = frappe.db.get_value("File", {"file_url": wm.attach}, "name")
	values = {"media_path": wm.attach if wm.attach.startswith("http") else get_url(wm.attach)}
	if file:
		values["attachment_file"] = file
	return values


def phone_number(value: str | None) -> str:
	"""A number as WhatsApp writes it (country code, digits only). Old rows keep numbers as typed
	("+91-9000012345", "9600471989"); a local one gets the company's country code."""
	from periskope_connector.linking import with_country_code

	return with_country_code(value)


def template_text(template: str | None, parameters: str | None = None) -> str:
	"""A template's body with its values, as the customer read it."""
	if not template:
		return ""
	body = frappe.db.get_value("WhatsApp Templates", template, "template") or ""
	try:
		values = json.loads(parameters) if parameters else []
	except ValueError:
		values = []
	if isinstance(values, dict):
		values = list(values.values())
	for i, value in enumerate(values or [], start=1):
		body = body.replace("{{" + str(i) + "}}", str(value))
	return body


# --- Sending -----------------------------------------------------------------------------------


def window(chat_name: str | None) -> dict:
	"""Whether Meta takes free text in this chat now: open for WINDOW_HOURS after the customer's
	last message."""
	last = chat_name and frappe.db.get_value("Periskope Chat", chat_name, "last_incoming_at")
	if not last:
		return {"open": False, "closes_at": None}
	closes = add_to_date(get_datetime(last), hours=WINDOW_HOURS)
	return {"open": closes > now_datetime(), "closes_at": str(closes)}


def send(
	chat_id: str,
	text: str,
	phone: str,
	media: dict | None = None,
	reply_to: str | None = None,
	template: str | None = None,
	params: list | None = None,
) -> dict:
	"""Send through frappe_whatsapp: a new Outgoing WhatsApp Message on the number's account. The
	test-mode check comes first. Returns {"unique_id": Meta's message id}."""
	safety.check_send(chat_id)
	acc = account_of(phone)
	if not acc or acc != account():
		frappe.throw(_("Meta WhatsApp is not connected for this number (Settings > Meta WhatsApp)."))
	to = digits(chat_id)
	chat_name = frappe.db.get_value("Periskope Chat", {"chat_id": chat_id, "org_phone": digits(phone)})
	if not template and not window(chat_name)["open"]:
		raise WindowClosed(
			_(
				"Meta takes a free message only within 24 hours of the customer's last message. "
				"Send an approved template instead."
			)
		)
	values = {
		"doctype": "WhatsApp Message",
		"type": "Outgoing",
		"to": to,
		"message": text or "",
		"content_type": "text",
		"whatsapp_account": acc,
	}
	if template:
		values.update(
			use_template=1,
			template=template,
			message_type="Template",
			body_param=json.dumps({str(i): v for i, v in enumerate(params or [], start=1)}),
		)
	if media and media.get("file"):
		values.update(content_type=_content_type(media.get("mimetype")), attach=public_link(media["file"]))
	if reply_to and reply_to.startswith(PREFIX):
		values.update(is_reply=1, reply_to_message_id=reply_to[len(PREFIX) :])
	record = chat_name and frappe.db.get_value(
		"Periskope Chat", chat_name, ["crm_deal", "crm_lead"], as_dict=True
	)
	if record and (record.crm_deal or record.crm_lead):
		# the CRM's own WhatsApp tab shows the message on the record too
		values.update(
			reference_doctype="CRM Deal" if record.crm_deal else "CRM Lead",
			reference_name=record.crm_deal or record.crm_lead,
		)
	doc = frappe.get_doc(values)
	doc.insert(ignore_permissions=True)  # frappe_whatsapp sends it to Meta here
	return {"unique_id": doc.message_id, "whatsapp_message": doc.name}


def _content_type(mimetype: str | None) -> str:
	kind = (mimetype or "").split("/")[0]
	return kind if kind in ("image", "video", "audio") else "document"


SHARE_PREFIX = "meta-share-"
SHARE_HOURS = 24  # Meta downloads a file when the message is sent; the public copy goes after this


def public_link(file_name: str) -> str:
	"""Meta fetches a file from a link, so a private file is sent as a public copy under a random
	name (the private one stays as it is). The copy is deleted after SHARE_HOURS
	(remove_shared_files)."""
	file = frappe.get_doc("File", file_name)
	if not file.is_private:
		return get_url(file.file_url)
	ext = re.sub(r"[^.\w]", "", ("." + file.file_name.rsplit(".", 1)[-1]) if "." in file.file_name else "")
	copy = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": SHARE_PREFIX + frappe.generate_hash(length=32) + ext,
			"is_private": 0,
			"content": file.get_content(),
		}
	).insert(ignore_permissions=True)
	return get_url(copy.file_url)


def remove_shared_files() -> int:
	"""Daily: delete the public copies made for Meta once Meta has had them (SHARE_HOURS)."""
	old = frappe.get_all(
		"File",
		filters={
			"file_name": ["like", SHARE_PREFIX + "%"],
			"is_private": 0,
			"creation": ["<", add_to_date(now_datetime(), hours=-SHARE_HOURS)],
		},
		pluck="name",
	)
	for name in old:
		try:
			frappe.delete_doc("File", name, ignore_permissions=True, force=True)
		except Exception:
			frappe.log_error(title=f"Periskope: could not delete the shared file {name}")
	return len(old)


def templates() -> list[dict]:
	"""The account's approved templates that need only text values (no header file)."""
	acc = account()
	if not acc:
		return []
	rows = frappe.get_all(
		"WhatsApp Templates",
		filters={"whatsapp_account": acc, "status": "APPROVED"},
		fields=["name", "template_name", "template", "category", "language_code", "header_type"],
		order_by="template_name asc",
	)
	out = []
	for r in rows:
		if (r.header_type or "TEXT").upper() not in ("", "TEXT"):
			continue
		count = len(set(re.findall(r"\{\{(\d+)\}\}", r.template or "")))
		out.append(
			{
				"name": r.name,
				"label": r.template_name or r.name,
				"body": r.template or "",
				"category": r.category,
				"language": r.language_code,
				"params": count,
			}
		)
	return out


# --- History -----------------------------------------------------------------------------------


def import_history(since: str | None = None) -> int:
	"""Copy the account's earlier messages, oldest first (Settings > Meta WhatsApp > Import history,
	a long background job). No AI, auto reply, welcome, contact queue, alert or SLA clock runs on them.
	At the end a chat is left open only when the customer wrote last within WINDOW_HOURS. Returns the
	number of messages copied."""
	acc = account()
	if not acc or not number():
		frappe.throw(_("Connect a Meta WhatsApp Account first."))
	filters = {"whatsapp_account": acc, "content_type": ["!=", "reaction"]}
	if since:
		filters["creation"] = [">=", since]
	total = frappe.db.count("WhatsApp Message", filters)
	done = copied = 0
	_status(_("Importing: 0 of {0}").format(total))
	while True:
		# oldest first, so a reply marks the customer's messages before it as answered
		page = frappe.get_all(
			"WhatsApp Message",
			filters=filters,
			pluck="name",
			order_by="creation asc, name asc",
			start=done,
			limit=IMPORT_BATCH,
		)
		if not page:
			break
		for name in page:
			if frappe.db.exists("Periskope Message", {"meta_message": name}):
				continue  # copied before (live, or by an earlier import)
			try:
				if ingest(name, history=True):
					copied += 1
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title=f"Periskope: could not import Meta WhatsApp message {name}")
		done += len(page)
		frappe.db.commit()
		_status(_("Importing: {0} of {1}").format(done, total))
	_settle_imported_chats()
	_status(_("Imported {0} messages on {1}.").format(copied, frappe.utils.format_datetime(now_datetime())))
	return copied


def queue_import(since: str | None = None) -> None:
	frappe.enqueue(
		"periskope_connector.meta.import_history",
		queue="long",
		timeout=6 * 3600,
		job_id="periskope-meta-import",
		deduplicate=True,
		since=since,
	)
	_status(_("Queued"))


def _settle_imported_chats():
	"""Chats made by the import (no SLA clock ever started on them) are closed, except where the
	customer wrote last within WINDOW_HOURS. Chats that live messages opened are left as they are."""
	recent = add_to_date(now_datetime(), hours=-WINDOW_HOURS)
	frappe.db.sql(
		"""update `tabPeriskope Chat` set status = 'Resolved'
		where whatsapp_number = %s and status = 'Open' and opened_on is null
		and not (last_message_direction = 'Incoming' and last_message_at >= %s)""",
		(number(), recent),
	)
	frappe.db.commit()


def _status(text: str):
	frappe.db.set_single_value("Periskope Settings", "meta_import_status", text)
	frappe.db.commit()
