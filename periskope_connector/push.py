"""Web push: the inbox's bell notifications also reach the user's phone or computer when the app is
closed (an installed app or a browser that allowed notifications).

- The site has one VAPID key pair, made on first use and kept in Periskope Settings.
- Each browser that turned notifications on is a Periskope Push Subscription of its user.
- Every Notification Log on a Periskope Chat (assignments, mentions, overdue replies, escalations)
  is pushed to its user's devices in a background job, unless the user turned device notifications
  off or it is their quiet time (Periskope Notification Setting). The bell itself always shows it.
- A device the push service says is gone (404/410) is removed.

The message carries only the bell's subject and the chat to open, never message text.
"""

import hashlib
import importlib.util
import json

import frappe
from frappe import _
from frappe.utils import get_time, now_datetime

GONE = (404, 410)
TTL_SECONDS = 6 * 3600  # an old nudge is not worth showing


def available() -> bool:
	"""Whether the web push library is installed (pyproject: pywebpush)."""
	return importlib.util.find_spec("pywebpush") is not None


def _keys() -> tuple[str, str]:
	"""(public key for the browser, private key), made once per site."""
	settings = frappe.get_single("Periskope Settings")
	public = settings.get("vapid_public_key")
	private = settings.get_password("vapid_private_key", raise_exception=False) if public else None
	if public and private:
		return public, private
	from cryptography.hazmat.primitives import serialization
	from py_vapid import Vapid, b64urlencode

	vapid = Vapid()
	vapid.generate_keys()
	private = b64urlencode(
		vapid.private_key.private_bytes(
			serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
		)
	)
	public = b64urlencode(
		vapid.public_key.public_bytes(
			serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
		)
	)
	from frappe.utils.password import set_encrypted_password

	# Stored directly: saving the whole settings would run their checks for an unrelated change.
	set_encrypted_password("Periskope Settings", "Periskope Settings", private, "vapid_private_key")
	frappe.db.set_single_value("Periskope Settings", "vapid_public_key", public)
	return public, private


def public_key() -> str:
	return _keys()[0]


def _hash(endpoint: str) -> str:
	return hashlib.sha256((endpoint or "").encode()).hexdigest()


def subscribe(user: str, subscription: dict, user_agent: str = "") -> str:
	"""Save (or refresh) one browser's subscription for the user. Returns its name."""
	endpoint = (subscription or {}).get("endpoint") or ""
	keys = (subscription or {}).get("keys") or {}
	if not endpoint.startswith("https://") or not keys.get("p256dh") or not keys.get("auth"):
		frappe.throw(_("This browser did not give a usable notification address."))
	key = _hash(endpoint)
	values = {
		"user": user,
		"endpoint": endpoint,
		"p256dh": keys["p256dh"],
		"auth": keys["auth"],
		"user_agent": (user_agent or "")[:140],
	}
	name = frappe.db.get_value("Periskope Push Subscription", {"endpoint_hash": key})
	if name:
		frappe.db.set_value("Periskope Push Subscription", name, values)
		return name
	# A system record of the user's own device: they never open it in Desk.
	return (
		frappe.get_doc({"doctype": "Periskope Push Subscription", "endpoint_hash": key, **values})
		.insert(ignore_permissions=True)
		.name
	)


def unsubscribe(user: str, endpoint: str) -> None:
	frappe.db.delete("Periskope Push Subscription", {"endpoint_hash": _hash(endpoint), "user": user})


def preferences(user: str) -> frappe._dict:
	row = frappe.db.get_value(
		"Periskope Notification Setting", user, ["push_off", "quiet_from", "quiet_to"], as_dict=True
	)
	return row or frappe._dict(push_off=0, quiet_from=None, quiet_to=None)


def quiet_now(prefs, now=None) -> bool:
	"""Whether it is the user's quiet time (it may run past midnight: 21:00 to 08:00)."""
	if not prefs.quiet_from or not prefs.quiet_to:
		return False
	start, end, t = get_time(prefs.quiet_from), get_time(prefs.quiet_to), (now or now_datetime()).time()
	return start <= t < end if start <= end else (t >= start or t < end)


def on_notification(doc, method=None):
	"""Notification Log after_insert: push a chat's bell to its user's devices (in the background)."""
	if doc.document_type != "Periskope Chat" or not doc.for_user:
		return
	if not frappe.db.exists("Periskope Push Subscription", {"user": doc.for_user}):
		return
	frappe.enqueue(
		"periskope_connector.push.send",
		log=doc.name,
		queue="short",
		enqueue_after_commit=True,
	)


def send(log: str) -> int:
	"""Background job: push one bell to its user's devices. Returns how many devices took it."""
	if not available():
		return 0
	from pywebpush import WebPushException, webpush

	from periskope_connector.webhooks import site_url

	row = frappe.db.get_value(
		"Notification Log", log, ["for_user", "subject", "document_name", "type"], as_dict=True
	)
	if not row:
		return 0
	prefs = preferences(row.for_user)
	if prefs.push_off or quiet_now(prefs):
		return 0
	_public, private = _keys()
	payload = json.dumps(
		{
			"title": frappe.utils.strip_html(row.subject or "")[:120] or _("WhatsApp inbox"),
			"url": f"/whatsapp/inbox/{row.document_name}",
			"tag": f"chat-{row.document_name}",
		}
	)
	sent = 0
	for sub in frappe.get_all(
		"Periskope Push Subscription",
		filters={"user": row.for_user},
		fields=["name", "endpoint", "p256dh", "auth"],
	):
		try:
			webpush(
				subscription_info={
					"endpoint": sub.endpoint,
					"keys": {"p256dh": sub.p256dh, "auth": sub.auth},
				},
				data=payload,
				vapid_private_key=private,
				vapid_claims={"sub": site_url()},
				ttl=TTL_SECONDS,
				timeout=10,
			)
			frappe.db.set_value(
				"Periskope Push Subscription", sub.name, "last_sent_on", now_datetime(), update_modified=False
			)
			sent += 1
		except WebPushException as e:
			if getattr(e.response, "status_code", None) in GONE:
				frappe.db.delete("Periskope Push Subscription", sub.name)  # the device unsubscribed
			else:
				from periskope_connector.health import log_once

				log_once("Periskope: a push notification could not be sent")
	return sent
