import hashlib
import hmac
import json

import frappe

from periskope_connector.messages import MESSAGE_CREATED


def _signatures(secret: str, raw: bytes) -> list[str]:
	"""Periskope's docs say it hashes the "stringified payload", which may be the
	raw body or a compact re-serialisation. Accept either until confirmed live."""
	candidates = [raw]
	try:
		candidates.append(json.dumps(json.loads(raw), separators=(",", ":")).encode())
	except ValueError:
		pass
	return [hmac.new(secret.encode(), c, hashlib.sha256).hexdigest() for c in candidates]


def _log(payload: dict, raw: bytes, valid: bool) -> str:
	return (
		frappe.get_doc(
			{
				"doctype": "Periskope Webhook Log",
				"event_type": payload.get("event_type"),
				"org_id": payload.get("org_id"),
				"event_timestamp": str(payload.get("timestamp") or ""),
				"signature_valid": 1 if valid else 0,
				"payload": raw.decode("utf-8", errors="replace")[:100000],
			}
		)
		.insert(ignore_permissions=True)
		.name
	)


@frappe.whitelist(allow_guest=True)
def receive() -> dict:
	raw = frappe.request.get_data() or b""
	try:
		payload = json.loads(raw)
		if not isinstance(payload, dict):
			payload = {}
	except ValueError:
		payload = {}

	secret = frappe.get_single("Periskope Settings").get_password(
		"webhook_signing_secret", raise_exception=False
	)
	provided = frappe.get_request_header("x-periskope-signature") or ""
	valid = bool(secret and provided) and any(
		hmac.compare_digest(sig, provided.strip().lower()) for sig in _signatures(secret, raw)
	)

	# Keep rejected calls too (truncated by _log) so the signature format can be debugged.
	log = _log(payload, raw, valid)
	if not valid:
		frappe.db.commit()  # throwing rolls back the log row
		frappe.local.response["http_status_code"] = 401
		frappe.throw("Invalid signature", frappe.AuthenticationError)

	if payload.get("event_type") == MESSAGE_CREATED:
		# Save in the background so Periskope gets its 200 straight away.
		frappe.enqueue(
			"periskope_connector.messages.process_webhook_log",
			log=log,
			queue="short",
			enqueue_after_commit=True,
		)

	return {"ok": True}
