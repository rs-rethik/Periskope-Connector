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
