"""Test mode: the guard every outgoing WhatsApp message, CRM write and email passes through.

While test mode is on (the default until an admin switches it off in Periskope Settings > Advanced):
- a WhatsApp message goes out only to a chat on the allow list. Every other 1-to-1 chat is a dry
  run: the reply is still prepared and logged, but nothing is sent. A group is always blocked, even
  when it is listed.
- the agent writes to the CRM only for allow-listed chats; for the rest it logs what it would do.
- an email reaches only the allow-listed addresses. The others are dropped from every queued email
  on the site, whatever app sent it.

Everything that is sent, dropped or only simulated is a Periskope Agent Action row, so it is always
possible to say exactly what left the site.
"""

import email
import email.header
import json
import re

import frappe
from frappe import _

from periskope_connector.system import insert_as_system

LIVE, DRY_RUN, BLOCKED = "Live", "Dry Run", "Blocked"
SETTINGS = "Periskope Settings"


class SendNotAllowed(frappe.ValidationError):
	"""Raised instead of sending; `mode` says whether it was a dry run or blocked."""

	def __init__(self, mode: str, message: str = ""):
		self.mode = mode
		super().__init__(message or _("Not sent: test mode ({0})").format(mode))


def _raw(field: str):
	"""The stored value, or None when the setting was never saved (get_single_value would turn a
	missing Check into 0, and a missing test_mode must count as on)."""
	singles = frappe.qb.DocType("Singles")
	rows = (
		frappe.qb.from_(singles)
		.select(singles.value)
		.where((singles.doctype == SETTINGS) & (singles.field == field))
		.run()
	)
	return rows[0][0] if rows else None


def test_mode() -> bool:
	"""On unless an admin switched it off. A site that never saved the setting counts as on."""
	value = _raw("test_mode")
	return value is None or str(value).strip() not in ("0", "")


def _digits(value: str | None) -> str:
	return re.sub(r"\D", "", (value or "").split("@")[0])


def _lines(raw: str | None) -> list[str]:
	return [v.strip() for v in re.split(r"[,\n;]", raw or "") if v.strip()]


def allowed_numbers() -> set[str]:
	return {_digits(v) for v in _lines(_raw("test_allowed_chats")) if _digits(v)}


def allowed_emails() -> set[str]:
	return {v.lower() for v in _lines(_raw("test_allowed_emails"))}


def is_group(chat_id: str | None) -> bool:
	return (chat_id or "").endswith("@g.us")


def send_mode(chat_id: str | None) -> str:
	"""Live, Dry Run or Blocked for a WhatsApp message to this chat."""
	if not test_mode():
		return LIVE
	if not chat_id or is_group(chat_id):
		return BLOCKED
	return LIVE if _digits(chat_id) in allowed_numbers() else DRY_RUN


def check_send(chat_id: str | None) -> None:
	"""Raise SendNotAllowed unless a message to this chat may really go out."""
	mode = send_mode(chat_id)
	if mode != LIVE:
		raise SendNotAllowed(mode)


def crm_mode(chat_id: str | None) -> str:
	"""Live or Dry Run for the agent's writes to the CRM records of this chat."""
	if not test_mode():
		return LIVE
	return LIVE if chat_id and not is_group(chat_id) and _digits(chat_id) in allowed_numbers() else DRY_RUN


def crm_write(chat_id: str | None, action: str, summary: str, fn, **values):
	"""Run `fn()` (a CRM write) when allowed and log it; in a dry run only log what it would do.
	Returns what `fn` returned, or None in a dry run."""
	mode = crm_mode(chat_id)
	if mode != LIVE:
		log(action, mode, summary, chat_id=chat_id, **values)
		return None
	result = fn()
	if isinstance(result, str) and not values.get("target_name"):
		values["target_name"] = result
	log(action, LIVE, summary, chat_id=chat_id, **values)
	return result


def log(action: str, mode: str, summary: str, **values) -> str | None:
	"""One Periskope Agent Action row. Never raises: logging must not stop the work it describes."""
	detail = values.pop("detail", None)
	try:
		doc = insert_as_system(
			{
				"doctype": "Periskope Agent Action",
				"action": action,
				"mode": mode,
				"summary": (summary or "")[:500],
				"detail": json.dumps(detail, default=str, indent=1) if detail is not None else None,
				**{k: v for k, v in values.items() if v not in (None, "")},
			}
		)
		return doc.name
	except Exception:
		frappe.log_error(title="Periskope: could not log an agent action")
		return None


# --- Email --------------------------------------------------------------------------------------


def _subject(doc) -> str:
	try:
		raw = email.message_from_string(doc.message or "")["Subject"] or ""
		return str(email.header.make_header(email.header.decode_header(raw)))
	except Exception:
		return ""


def filter_email_queue(doc, method=None):
	"""Email Queue before_insert: in test mode drop every recipient not on the allow list. An email
	left with nobody is kept as an Error row (never sent), so it is still visible."""
	if not test_mode():
		return
	allowed = allowed_emails()
	keep = [r for r in doc.recipients if (r.recipient or "").strip().lower() in allowed]
	dropped = [r.recipient for r in doc.recipients if r not in keep]
	subject = _subject(doc)
	if not keep:
		doc.set("recipients", [])
		doc.status = "Error"
		doc.error = "Blocked by Periskope test mode: no recipient is on the allow list."
		log("Email", BLOCKED, f"Email not sent: {subject}", origin="Email", detail={"dropped": dropped})
		return
	doc.set("recipients", keep)
	log(
		"Email",
		LIVE,
		f"Email queued: {subject}",
		origin="Email",
		detail={"to": [r.recipient for r in keep], "dropped": dropped},
	)
