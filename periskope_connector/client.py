import time

import frappe
import requests
from frappe import _

TIMEOUT = 20
# A busy or briefly unavailable Periskope is asked again, a little later each time. A POST is asked
# again only for 429 (refused before doing anything): after a 5xx a message may already be on its way.
RETRY_WAITS = (1, 3)  # seconds before the 2nd and 3rd try
RETRY_STATUSES = (429, 502, 503, 504)
MAX_RETRY_AFTER = 10


def request(
	method: str,
	path: str,
	phone: str | None = None,
	json: dict | None = None,
	text: bool = False,
	missing_ok: bool = False,
	params: dict | None = None,
):
	"""Call the Periskope API and return the decoded JSON body (`text`: the body as text, for the
	endpoints that answer HTML, such as the QR code).

	`phone` is the WhatsApp number (digits with country code), or the phone's id, for endpoints that
	act on one linked number; it is sent as the `x-phone` header. With `missing_ok` a 404 returns None
	(a QR code that is not there right now). `params` are the query string (GET filters).
	"""
	settings = frappe.get_single("Periskope Settings")
	api_key = settings.get_password("api_key", raise_exception=False)
	if not api_key:
		frappe.throw(_("Periskope API Key is not set in Periskope Settings"))
	if not settings.base_url:
		frappe.throw(_("Periskope API Base URL is not set in Periskope Settings"))

	headers = {"Authorization": f"Bearer {api_key}"}
	if phone:
		headers["x-phone"] = phone

	safe_to_repeat = method.upper() in ("GET", "HEAD")
	for attempt in range(len(RETRY_WAITS) + 1):
		last_try = attempt == len(RETRY_WAITS)
		try:
			response = requests.request(
				method,
				f"{settings.base_url}/{path.lstrip('/')}",
				headers=headers,
				json=json,
				params=params,
				timeout=TIMEOUT,
			)
		except requests.RequestException as e:
			if safe_to_repeat and not last_try:
				time.sleep(RETRY_WAITS[attempt])
				continue
			frappe.throw(_("Could not reach Periskope: {0}").format(type(e).__name__))
		again = response.status_code == 429 or (safe_to_repeat and response.status_code in RETRY_STATUSES)
		if not again or last_try:
			break
		wait = RETRY_WAITS[attempt]
		if response.status_code == 429:
			wait = min(MAX_RETRY_AFTER, frappe.utils.cint(response.headers.get("Retry-After")) or wait)
		time.sleep(wait)

	if response.status_code in (401, 403):
		frappe.throw(_("Periskope rejected the API key (HTTP {0})").format(response.status_code))
	if response.status_code == 404 and missing_ok:
		return None
	if not response.ok:
		frappe.throw(_("Periskope returned HTTP {0}: {1}").format(response.status_code, response.text[:200]))
	return response.text if text else response.json()


def send_message(
	chat_id: str, text: str, phone: str, media: dict | None = None, reply_to: str | None = None
) -> dict:
	"""Send a WhatsApp message from one of the linked numbers (POST /message/send). The reply carries
	a queue id, not the final delivery state: Periskope queues the message and sends it itself.

	`media` is Periskope's media object ({type, filedata (base64), mimetype, filename}); `text` is then
	its caption and may be empty. `reply_to` is the message_id of the message being answered.

	The test-mode check comes first and cannot be skipped: a chat that is not allowed raises
	safety.SendNotAllowed before any request is made. Call it through outbound.send, which logs."""
	from periskope_connector import safety

	safety.check_send(chat_id)
	if not chat_id or not phone or not (text or media):
		frappe.throw(_("A chat, a text or file and a sending number are needed to send a message"))
	body = {"chat_id": chat_id, "message": text or ""}
	if media:
		body["media"] = media
	if reply_to:
		body["reply_to"] = reply_to
	return request("POST", "message/send", phone=phone, json=body)


def fetch_chat_name(chat_id: str, phone: str | None = None) -> str:
	"""The name of a WhatsApp chat from Periskope (the group name, or a contact's name), or "".

	Webhooks carry only the chat id, so the name is looked up once per group and remembered for a day.
	Never raises: a missing name must not stop a message from being saved.
	"""
	if not chat_id:
		return ""
	cache = frappe.cache()
	key = f"periskope_chat_name:{chat_id}"
	name = cache.get_value(key)
	if name is not None:
		return name
	try:
		data = request("GET", f"chats?chat_id={chat_id}", phone=phone)
		chats = data.get("chats", []) if isinstance(data, dict) else []
		name = next((c.get("chat_name") for c in chats if c.get("chat_id") == chat_id), "") or ""
	except frappe.ValidationError:
		return ""  # not set up (no API key) or Periskope refused: nothing to report per message
	except Exception:
		frappe.log_error(title="Periskope: could not fetch the chat name")
		return ""
	cache.set_value(key, name, expires_in_sec=86400)
	return name


def fetch_member_numbers(chat_id: str, phone: str | None = None) -> list[str]:
	"""The numbers of a WhatsApp group's members (digits with country code) from Periskope, or [].

	Members who never wrote in the group are known only from here. Never raises: a group that cannot
	be read is simply looked at again later.
	"""
	try:
		data = request("GET", f"chats/{chat_id}", phone=phone) or {}
	except frappe.ValidationError:
		return []  # not set up, or Periskope refused this chat for this number
	except Exception:
		frappe.log_error(title="Periskope: could not fetch the group members")
		return []
	chat = data.get("chat", data) if isinstance(data, dict) else {}
	members = chat.get("members") or {}
	items = members.values() if isinstance(members, dict) else members
	numbers = (
		"".join(ch for ch in str(m.get("contact_id") or "").split("@")[0] if ch.isdigit()) for m in items
	)
	return [n for n in numbers if n]
