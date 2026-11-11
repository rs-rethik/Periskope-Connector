"""Connect or re-link a Periskope number from the WhatsApp app (Settings > WhatsApp numbers),
instead of scanning its QR code in the Periskope console. Admins only: whoever scans the code links
that company WhatsApp.

- state(): the number's WhatsApp state from Periskope (CONNECTED, or UNPAIRED while it waits for a
  scan), saved on the WhatsApp Number.
- qr(): the QR code to scan (WhatsApp > Settings > Linked devices), as an image; the app asks again
  every few seconds, as the code changes and until the number is connected.
- relink(): log the number's WhatsApp out (Periskope's reset) so another phone can be linked.
- restart(): restart the number's instance in Periskope, for a number that is stuck.
- queue(): the number's send queue (GET /message/queues/health): running, waiting, failed.
- purge_queue(): empty the number's send queue (POST /message/queues/purge); the inbox messages that
  had not reached WhatsApp are marked failed, so they can be sent again with Retry.
- add(): add a new phone to the Periskope account (Periskope's create), then its QR code is shown by
  qr(phone_id=...). It has no number until a WhatsApp scans the code; then the numbers are synced and
  it shows in the list. A phone added earlier but never scanned is used again, so an abandoned try
  does not add (and bill) a second phone.

A Meta WhatsApp number is linked in Meta, not here.
"""

import re

import frappe
from frappe import _
from frappe.utils import now_datetime

from periskope_connector import access, numbers
from periskope_connector.api.admin import ADMIN, _require
from periskope_connector.client import request

CONNECTED = "CONNECTED"
PHONE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
QR_IMAGE = re.compile(r"data:image/(?:png|jpeg|svg\+xml);base64,[A-Za-z0-9+/=]+")


def _number(number: str) -> frappe._dict:
	"""The WhatsApp Number and what Periskope knows it by (its phone id, else the number)."""
	row = frappe.db.get_value(
		"WhatsApp Number", number, ["name", "source", "periskope_phone_id"], as_dict=True
	)
	if not row:
		frappe.throw(_("Unknown WhatsApp number {0}").format(number))
	if row.source == "Meta":
		frappe.throw(_("A Meta WhatsApp number is linked in Meta, not here."))
	row.x_phone = row.periskope_phone_id or row.name
	return row


def _new_phone(phone_id: str) -> frappe._dict:
	"""A phone added here that has no WhatsApp Number yet (no number until it is scanned)."""
	if not PHONE_ID.match(phone_id or ""):
		frappe.throw(_("Unknown Periskope phone {0}").format(phone_id))
	return frappe._dict(name=None, x_phone=phone_id)


def _target(number: str | None, phone_id: str | None) -> frappe._dict:
	return _number(number) if number else _new_phone(phone_id)


def _is_connected(phone: dict) -> bool:
	return (phone or {}).get("wa_state") == CONNECTED and bool(phone.get("is_ready", True))


def _save(row, phone: dict) -> dict:
	"""Keep the WhatsApp Number in step with Periskope's answer. Returns the state for the app."""
	state = (phone or {}).get("wa_state") or ""
	connected = _is_connected(phone)
	if not row.name:  # a new phone: nothing saved until it has a number
		return {"state": state or "NOT LINKED", "connected": connected}
	frappe.db.set_value(
		"WhatsApp Number",
		row.name,
		{
			"wa_state": state or "NOT LINKED",
			"is_ready": 1 if connected else 0,
			"last_sync_on": now_datetime(),
			"disconnected_since": None
			if connected
			else (frappe.db.get_value("WhatsApp Number", row.name, "disconnected_since") or now_datetime()),
		},
	)
	frappe.db.commit()
	return {"state": state or "NOT LINKED", "connected": connected}


def _phone(row) -> dict:
	data = request("GET", "phones", phone=row.x_phone) or {}
	return data.get("phone", data) if isinstance(data, dict) else {}


@frappe.whitelist()
def state(number: str) -> dict:
	"""The number's WhatsApp state, read from Periskope now."""
	access.require_inbox_user()
	_require(ADMIN)
	row = _number(number)
	return _save(row, _phone(row))


@frappe.whitelist()
def qr(number: str | None = None, phone_id: str | None = None) -> dict:
	"""The QR code to link the number (or a phone just added, by its phone_id), while it waits for a
	scan: {state, connected, qr, number}. qr is an image (data URI), or None when there is none right
	now (connected, or still being made). number is the WhatsApp number once connected."""
	access.require_inbox_user()
	_require(ADMIN)
	row = _target(number, phone_id)
	phone = _phone(row)
	out = _save(row, phone)
	out["qr"] = None
	out["number"] = row.name
	if out["connected"]:
		_refresh_numbers()
		out["number"] = row.name or numbers.digits(phone.get("org_phone")) or None
		return out
	html = request("GET", "phones/qr", phone=row.x_phone, text=True, missing_ok=True)
	found = QR_IMAGE.search(html or "")
	out["qr"] = found.group(0) if found else None  # only the image, never Periskope's HTML
	return out


@frappe.whitelist(methods=["POST"])
def relink(number: str) -> dict:
	"""Log the number's WhatsApp out, so it can be linked again (to the same or another phone)."""
	access.require_inbox_user()
	_require(ADMIN)
	row = _number(number)
	request("POST", "phones/reset", phone=row.x_phone)
	return _save(row, {"wa_state": "UNPAIRED", "is_ready": False})


@frappe.whitelist(methods=["POST"])
def restart(number: str | None = None, phone_id: str | None = None) -> dict:
	"""Restart the number's (or new phone's) instance in Periskope (for one that is stuck)."""
	access.require_inbox_user()
	_require(ADMIN)
	row = _target(number, phone_id)
	request("POST", "phones/restart", phone=row.x_phone)
	return _save(row, _phone(row))


@frappe.whitelist(methods=["POST"])
def add() -> dict:
	"""Add a new phone to the Periskope account, to be linked by its QR code: {phone_id, reused}.
	A phone added before and never scanned (no number) is used again instead of adding another."""
	access.require_inbox_user()
	_require(ADMIN)
	from periskope_connector.api.settings import _fetch_phones

	for phone in _fetch_phones():
		if not phone.get("org_phone") and not phone.get("first_connected_at") and phone.get("phone_id"):
			return {"phone_id": phone["phone_id"], "reused": True}
	phone = request("POST", "phones/create") or {}
	phone = phone.get("phone", phone) if isinstance(phone, dict) else {}
	if not phone.get("phone_id"):
		frappe.throw(_("Periskope did not add the phone. Check the plan's phone limit in Periskope."))
	return {"phone_id": phone["phone_id"], "reused": False}


@frappe.whitelist()
def queue(number: str) -> dict:
	"""The number's send queue: {running, active, pending, completed, failed}."""
	access.require_inbox_user()
	_require(ADMIN)
	row = _number(number)
	data = request("GET", "message/queues/health", phone=row.x_phone) or {}
	return {
		"running": bool(data.get("is_running")),
		"active": int(data.get("active") or 0),
		"pending": int(data.get("pending") or 0),
		"completed": int(data.get("completed") or 0),
		"failed": int(data.get("failed") or 0),
	}


@frappe.whitelist(methods=["POST"])
def purge_queue(number: str) -> dict:
	"""Remove every message still waiting in the number's send queue (not those WhatsApp has). The
	inbox messages among them are marked failed, with the reason, so a rep can send them again."""
	access.require_inbox_user()
	_require(ADMIN)
	row = _number(number)
	request("POST", "message/queues/purge", phone=row.x_phone, json={})
	# Only what was still waiting: a reply WhatsApp has (an ack came back) was sent, even when
	# Periskope's copy of it has not arrived yet, and Retry would send it a second time.
	msg = frappe.qb.DocType("Periskope Message")
	waiting = (
		frappe.qb.from_(msg)
		.select(msg.name)
		.where(
			(msg.whatsapp_number == row.name)
			& (msg.direction == "Outgoing")
			& msg.send_status.isin(["Sending", "Queued"])
			& msg.message_id.like("local\\_%")
			& (msg.ack.isnull() | msg.ack.isin(["", "0"]))
		)
	).run(pluck=True)
	reason = _("Removed from the send queue by {0}; send it again with Retry.").format(
		frappe.utils.get_fullname(frappe.session.user)
	)
	if waiting:
		(
			frappe.qb.update(msg)
			.set(msg.send_status, "Failed")
			.set(msg.send_error, reason)
			.where(msg.name.isin(waiting))
		).run()
	frappe.db.commit()
	return {"cancelled": len(waiting), "queue": queue(number)}


def _refresh_numbers():
	"""After a scan the number may have changed (another phone was linked): read the list again."""
	try:
		from periskope_connector.api.settings import _fetch_phones

		numbers.sync_from_periskope(_fetch_phones())
		frappe.db.commit()
	except Exception:
		frappe.log_error(title="Periskope: could not refresh the numbers after linking")
