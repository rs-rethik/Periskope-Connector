import hashlib
import hmac
import json

import frappe
from frappe.rate_limiter import rate_limit

from periskope_connector.messages import MESSAGE_CREATED
from periskope_connector.system import insert_as_system

# Far above what a few linked phones send, so it only stops floods.
RATE_LIMIT = 3000
RATE_LIMIT_SECONDS = 60


def _signatures(secret: str, raw: bytes) -> list[str]:
	"""Periskope's docs say it hashes the "stringified payload", which may be the
	raw body or a compact re-serialisation. Accept either."""
	candidates = [raw]
	try:
		candidates.append(json.dumps(json.loads(raw), separators=(",", ":")).encode())
	except ValueError:
		pass
	return [hmac.new(secret.encode(), c, hashlib.sha256).hexdigest() for c in candidates]


def _has_valid_signature(raw: bytes) -> bool:
	secret = frappe.get_single("Periskope Settings").get_password(
		"webhook_signing_secret", raise_exception=False
	)
	provided = (frappe.get_request_header("x-periskope-signature") or "").strip().lower()
	if not secret or not provided:
		return False
	return any(hmac.compare_digest(sig, provided) for sig in _signatures(secret, raw))


def _log(payload: dict, raw: bytes) -> str:
	# Only reached after the HMAC signature has been verified; the caller is a guest.
	return insert_as_system(
		{
			"doctype": "Periskope Webhook Log",
			"event_type": payload.get("event_type"),
			"org_id": payload.get("org_id"),
			"event_timestamp": str(payload.get("timestamp") or ""),
			"signature_valid": 1,
			"payload": raw.decode("utf-8", errors="replace")[:100000],
		}
	).name


@frappe.whitelist(allow_guest=True)
@rate_limit(limit=RATE_LIMIT, seconds=RATE_LIMIT_SECONDS)
def receive() -> dict:
	"""Periskope webhook.

	Guests must be allowed in because Periskope calls this with no Frappe login, so a role
	check cannot be used here. The shared-secret HMAC signature is the authentication, and
	nothing is written to the database until it has been verified.
	"""
	raw = frappe.request.get_data() or b""

	if not _has_valid_signature(raw):
		frappe.logger("periskope_connector").warning(
			"Rejected a Periskope webhook call with a missing or invalid signature "
			f"(ip: {frappe.local.request_ip})"
		)
		frappe.local.response["http_status_code"] = 401
		frappe.throw("Invalid signature", frappe.AuthenticationError)

	try:
		payload = json.loads(raw)
	except ValueError:
		payload = {}
	if not isinstance(payload, dict):
		payload = {}

	log = _log(payload, raw)
	if payload.get("event_type") == MESSAGE_CREATED:
		# Save in the background so Periskope gets its 200 straight away.
		frappe.enqueue(
			"periskope_connector.messages.process_webhook_log",
			log=log,
			queue="short",
			enqueue_after_commit=True,
		)

	return {"ok": True}
