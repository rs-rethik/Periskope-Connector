"""Our WhatsApp numbers: one WhatsApp Number row per number connected in Periskope, and the number of
a Meta WhatsApp Account when one is connected (source "Meta", meta.py).

The rows come from Periskope (sync_from_periskope, via Test Connection or Sync Numbers). A message on
a number that is not saved yet adds it (ensure), so every chat and message can point to its number.
Admins set the display name, the default rep and whether the number is active.
"""

import frappe
from frappe.utils import now_datetime

from periskope_connector import safety
from periskope_connector.periskope_connector.doctype.whatsapp_number.whatsapp_number import digits
from periskope_connector.system import insert_as_system

DOCTYPE = "WhatsApp Number"
FIELDS = [
	"name",
	"number",
	"display_name",
	"phone_name",
	"is_active",
	"is_ready",
	"wa_state",
	"default_assignee",
	"sales_person",
	"periskope_phone_id",
	"disconnected_since",
	"last_sync_on",
	"last_message_on",
	"source",
]
NOT_META = ["source", "!=", "Meta"]  # Periskope's numbers (older rows have no source)


def ensure(number: str | None) -> str | None:
	"""The WhatsApp Number row of one of our numbers, added when it is new. None for no number.
	Never raises: a message must be saved even if its number row cannot be."""
	number = digits(number)
	if not number:
		return None
	if frappe.db.exists(DOCTYPE, number):
		return number
	try:
		frappe.db.savepoint("periskope_new_number")
		insert_as_system({"doctype": DOCTYPE, "number": number, "display_name": number})
		return number
	except frappe.DuplicateEntryError:
		frappe.db.rollback(save_point="periskope_new_number")
		return number
	except Exception:
		frappe.db.rollback(save_point="periskope_new_number")
		frappe.log_error(title=f"Periskope: could not save the WhatsApp number {number}")
		return None


def rep(number: str | None) -> str | None:
	"""The default rep for chats on this number."""
	return frappe.db.get_value(DOCTYPE, digits(number), "default_assignee") if number else None


def sales_person(number: str | None) -> str | None:
	return frappe.db.get_value(DOCTYPE, digits(number), "sales_person") if number else None


def ready() -> list[str]:
	"""The Periskope numbers that are connected and ready, for reading history and sending."""
	return frappe.get_all(
		DOCTYPE,
		filters=[["is_ready", "=", 1], ["is_active", "=", 1], NOT_META],
		pluck="name",
		order_by="name asc",
	)


def first_ready() -> str | None:
	numbers = ready()
	return numbers[0] if numbers else None


def all_numbers(active_only: bool = False) -> list[frappe._dict]:
	filters = {"is_active": 1} if active_only else {}
	return frappe.get_all(DOCTYPE, filters=filters, fields=FIELDS, order_by="number asc")


def sync_from_periskope(phones: list[dict]) -> list[frappe._dict]:
	"""Save Periskope's phone list (GET phones/all): new numbers are added, known ones refreshed.
	What admins set (display name, rep, active) is kept. A number that dropped off the list or lost
	its connection is marked not ready, with the time it was first seen disconnected."""
	now = now_datetime()
	seen = set()
	for phone in phones:
		number = digits(phone.get("org_phone") or phone.get("number"))
		if not number:
			continue
		seen.add(number)
		is_ready = 1 if phone.get("is_ready") else 0
		values = {
			"periskope_phone_id": phone.get("phone_id"),
			"phone_name": phone.get("phone_name"),
			"wa_state": phone.get("wa_state"),
			"is_ready": is_ready,
			"last_sync_on": now,
		}
		name = ensure(number)
		if not name:
			continue
		row = frappe.db.get_value(DOCTYPE, name, ["display_name", "disconnected_since"], as_dict=True)
		if not row.display_name or row.display_name == number:
			values["display_name"] = phone.get("phone_name") or number
		values["disconnected_since"] = None if is_ready else (row.disconnected_since or now)
		frappe.db.set_value(DOCTYPE, name, values)
	for row in frappe.get_all(
		DOCTYPE,
		filters=[["name", "not in", list(seen) or [""]], NOT_META],
		fields=["name", "disconnected_since"],
	):
		frappe.db.set_value(
			DOCTYPE,
			row.name,
			{
				"is_ready": 0,
				"wa_state": "NOT LINKED",
				"last_sync_on": now,
				"disconnected_since": row.disconnected_since or now,
			},
		)
	return all_numbers()


def label(number: str | None) -> str:
	"""How a number is shown: its display name, else the number."""
	if not number:
		return ""
	return frappe.db.get_value(DOCTYPE, digits(number), "display_name") or digits(number)


def health_check() -> list[str]:
	"""Scheduler, every 15 minutes: read the numbers' connection state from Periskope. A number that
	was connected and now is not alerts the managers once (a bell, and an email to the alert list);
	when it is back, the next disconnection alerts again. Returns the numbers that just dropped."""
	settings = frappe.get_single("Periskope Settings")
	if not settings.enabled or not settings.get_password("api_key", raise_exception=False):
		return []
	before = {n.name: n.is_ready for n in all_numbers()}
	try:
		from periskope_connector.api.settings import _fetch_phones

		after = {n.name: n for n in sync_from_periskope(_fetch_phones())}
	except Exception:
		from periskope_connector.health import log_once

		log_once("Periskope: could not read the numbers' connection state")
		return []
	dropped = [n for n, ready in before.items() if ready and n in after and not after[n].is_ready]
	if dropped:
		_alert_dropped([after[n] for n in dropped])
	frappe.db.commit()
	return dropped


def _alert_dropped(rows: list):
	from frappe import _

	from periskope_connector import reminders
	from periskope_connector.access import managers_by_number, managers_of
	from periskope_connector.sla import _bell

	names = ", ".join(f"{r.display_name or r.number} (+{r.number})" for r in rows)
	subject = _("WhatsApp number disconnected: {0}").format(names)
	managers = managers_by_number()
	for row in rows:
		# The bell opens the number's newest chat, the closest page the inbox has for a number.
		chat = frappe.db.get_value(
			"Periskope Chat", {"whatsapp_number": row.name}, "name", order_by="last_message_at desc"
		)
		if chat:
			_bell(managers_of(row.name, managers), chat, subject)
	emails = reminders.get_config().emails
	if emails:
		safety.sendmail(
			recipients=emails,
			subject=subject,
			message=_(
				"Periskope reports {0} as not connected. Customers' messages to it are not received until it is "
				"linked again in Periskope."
			).format(names),
			delayed=True,
		)
