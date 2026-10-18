import frappe
import requests
from frappe import _

TIMEOUT = 20


def request(method: str, path: str, phone: str | None = None, json: dict | None = None):
	"""Call the Periskope API and return the decoded JSON body.

	`phone` is the WhatsApp number (digits with country code) for endpoints
	that act on one linked number; it is sent as the `x-phone` header.
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

	try:
		response = requests.request(
			method,
			f"{settings.base_url}/{path.lstrip('/')}",
			headers=headers,
			json=json,
			timeout=TIMEOUT,
		)
	except requests.RequestException as e:
		frappe.throw(_("Could not reach Periskope: {0}").format(type(e).__name__))

	if response.status_code in (401, 403):
		frappe.throw(_("Periskope rejected the API key (HTTP {0})").format(response.status_code))
	if not response.ok:
		frappe.throw(_("Periskope returned HTTP {0}: {1}").format(response.status_code, response.text[:200]))
	return response.json()


def send_message(chat_id: str, text: str, phone: str) -> dict:
	"""Send a WhatsApp text from one of the linked numbers (POST /message/send). The reply carries a
	queue id, not the final delivery state: Periskope queues the message and sends it itself.

	The test-mode check comes first and cannot be skipped: a chat that is not allowed raises
	safety.SendNotAllowed before any request is made. Call it through outbound.send, which logs."""
	from periskope_connector import safety

	safety.check_send(chat_id)
	if not chat_id or not text or not phone:
		frappe.throw(_("A chat, a text and a sending number are needed to send a message"))
	return request("POST", "message/send", phone=phone, json={"chat_id": chat_id, "message": text})


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
