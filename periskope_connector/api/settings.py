import frappe
from frappe import _
from frappe.utils import now_datetime

from periskope_connector.client import request


def _fetch_phones() -> list[dict]:
	data = request("GET", "phones/all")
	# Docs describe a flat array; tolerate a wrapped one.
	return data if isinstance(data, list) else data.get("phones", [])


def _set_status(status: str):
	"""Save the connection status right away. test_connection() calls this after an error, when
	the request is about to fail and would roll everything back. A problem saving the status must
	not replace the error the user needs to see, so it is logged instead of raised."""
	try:
		frappe.db.set_single_value(
			"Periskope Settings",
			{"connection_status": status, "last_checked": now_datetime()},
		)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Periskope: could not save the connection status")


@frappe.whitelist()
def test_connection() -> dict:
	frappe.only_for("System Manager")
	try:
		phones = _fetch_phones()
		_save_phones(phones)
	except Exception as e:
		# The failed request rolls everything back, so the status is saved on its own afterwards.
		frappe.db.rollback()
		_set_status(f"Failed: {str(e)[:100]}")
		raise
	_set_status(f"Connected ({len(phones)} phone(s))")
	result = {"phone_count": len(phones)}
	try:
		result["webhooks"] = setup_webhooks()
	except Exception as e:
		# A webhook that could not be registered must not hide that the connection itself works.
		result["webhooks_error"] = str(e)[:300]
	return result


@frappe.whitelist()
def setup_webhooks() -> dict:
	"""Register this site's webhook in Periskope (message.created and message.updated)."""
	frappe.only_for("System Manager")
	from periskope_connector.webhooks import ensure_webhooks

	return ensure_webhooks()


@frappe.whitelist()
def run_reminder_check() -> dict:
	frappe.only_for("System Manager")
	from periskope_connector.reminders import run

	return run()


@frappe.whitelist()
def optimize_ai_instructions(instructions: str = "", goal: str = "") -> str:
	frappe.only_for("System Manager")
	from periskope_connector.ai.classifier import get_settings, optimize_instructions

	return optimize_instructions(get_settings(), instructions, goal)


@frappe.whitelist()
def get_default_ai_instructions() -> str:
	frappe.only_for("System Manager")
	from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS

	return DEFAULT_INSTRUCTIONS


@frappe.whitelist()
def test_ai_connection() -> dict:
	frappe.only_for("System Manager")
	from periskope_connector.ai.classifier import get_settings, ping

	return {"model": ping(get_settings())}


@frappe.whitelist()
def sync_phones() -> list[dict]:
	frappe.only_for("System Manager")
	return _save_phones(_fetch_phones())


def _save_phones(phones: list[dict]) -> list[dict]:
	settings = frappe.get_doc("Periskope Settings")
	assigned = {row.phone_id: (row.rep, row.sales_person) for row in settings.phones}
	settings.set("phones", [])
	for phone in phones:
		rep, sales_person = assigned.get(phone.get("phone_id"), (None, None))
		settings.append(
			"phones",
			{
				"phone_id": phone.get("phone_id"),
				"phone_name": phone.get("phone_name"),
				"number": (phone.get("org_phone") or "").split("@")[0],
				"wa_state": phone.get("wa_state"),
				"is_ready": 1 if phone.get("is_ready") else 0,
				"rep": rep,
				"sales_person": sales_person,
			},
		)
	settings.save()
	return [row.as_dict() for row in settings.phones]


@frappe.whitelist()
def set_correct_category(message: str, category: str) -> None:
	"""A sales manager marks the category the AI should have given. Kept for the later learning loop"""
	frappe.only_for(("Sales Manager", "System Manager"))
	from periskope_connector.tags import names

	if category not in names():
		frappe.throw(_("Category must be one of: {0}").format(", ".join(names())))
	frappe.db.set_value("Periskope Message", message, "correct_category", category)


@frappe.whitelist()
def get_health() -> dict:
	"""The live figures for the Health tab of the settings page."""
	frappe.only_for(("System Manager", "WhatsApp Manager"))
	from periskope_connector.health import get_health as figures

	return figures()


@frappe.whitelist()
def backfill_messages(days: int = 7) -> dict:
	"""Fetch the last `days` days of chat history from Periskope and save what webhooks missed."""
	frappe.only_for("System Manager")
	from periskope_connector.backfill import run

	return run(days=min(max(int(days), 1), 30))


@frappe.whitelist()
def get_flow() -> dict:
	"""The health and figures of each step, for the Flow tab of the settings page."""
	frappe.only_for(("System Manager", "WhatsApp Manager"))
	from periskope_connector.flow import get_flow as figures

	return figures()


@frappe.whitelist()
def set_tag_active(tag: str, active: int = 1) -> None:
	"""Flow tab: switch a tag on or off."""
	frappe.only_for("System Manager")
	from periskope_connector.flow import set_tag_active as switch

	switch(tag, bool(int(active)))


@frappe.whitelist()
def set_tag_action(tag: str, action: str, on: int = 1) -> None:
	"""Flow tab: give a tag an action, or take it away."""
	frappe.only_for("System Manager")
	from periskope_connector.flow import set_tag_action as change

	change(tag, action, bool(int(on)))


@frappe.whitelist()
def set_flow_setting(field: str, value=None) -> None:
	"""Flow dialog: change one setting and save it."""
	frappe.only_for("System Manager")
	from periskope_connector.flow import set_setting

	set_setting(field, value)


@frappe.whitelist()
def set_flow_row(table: str, idx: int, field: str, value=None) -> None:
	"""Flow dialog: change one field of an SLA level or keyword rule row and save."""
	frappe.only_for("System Manager")
	from periskope_connector.flow import set_row

	set_row(table, idx, field, value)


@frappe.whitelist()
def add_flow_sla_level() -> None:
	"""Flow dialog: add an SLA step."""
	frappe.only_for("System Manager")
	from periskope_connector.flow import add_sla_level

	add_sla_level()


@frappe.whitelist()
def remove_flow_sla_level(idx: int) -> None:
	"""Flow dialog: remove an SLA step."""
	frappe.only_for("System Manager")
	from periskope_connector.flow import remove_sla_level

	remove_sla_level(idx)
