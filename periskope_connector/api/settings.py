import frappe
from frappe import _
from frappe.utils import now_datetime

from periskope_connector import access
from periskope_connector.api.permission import require_admin, require_manager
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
	require_admin()
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
		_set_webhook_status(_("Registered"))
	except Exception as e:
		# A webhook that could not be registered must not hide that the connection itself works, but
		# it is kept, so setup step 1 is not green while nothing can arrive.
		result["webhooks_error"] = str(e)[:300]
		_set_webhook_status(f"Failed: {str(e)[:120]}")
	return result


def _set_webhook_status(status: str):
	frappe.db.set_single_value("Periskope Settings", "webhook_status", status)


@frappe.whitelist()
def setup_webhooks() -> dict:
	"""Register this site's webhook in Periskope (message.created and message.updated)."""
	require_admin()
	from periskope_connector.webhooks import ensure_webhooks

	return ensure_webhooks()


@frappe.whitelist()
def test_ai_connection() -> dict:
	require_admin()
	from periskope_connector.ai.classifier import get_settings, ping

	try:
		model = ping(get_settings())
	except Exception as e:
		frappe.db.rollback()
		_save_ai_status(f"Failed: {str(e)[:100]}")
		raise
	_save_ai_status(f"Working ({model})")
	return {"model": model}


def _save_ai_status(status: str):
	try:
		frappe.db.set_single_value("Periskope Settings", "ai_status", status)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Periskope: could not save the AI status")


@frappe.whitelist()
def get_setup_status() -> dict:
	"""The setup steps for the panel at the top of the settings."""
	require_admin()
	from periskope_connector.setup_status import get_status

	return get_status()


@frappe.whitelist()
def sync_phones() -> list[dict]:
	require_admin()
	return _save_phones(_fetch_phones())


def _save_phones(phones: list[dict]) -> list[dict]:
	from periskope_connector import numbers

	return numbers.sync_from_periskope(phones)


@frappe.whitelist()
def set_correct_category(message: str, category: str) -> None:
	"""A sales manager marks the category the AI should have given. Kept for the later learning loop"""
	require_manager()
	access.require_message(message)
	from periskope_connector.tags import names

	if category not in names():
		frappe.throw(_("Category must be one of: {0}").format(", ".join(names())))
	frappe.db.set_value("Periskope Message", message, "correct_category", category)


@frappe.whitelist(methods=["GET", "POST"])  # the inbox's call() reads with POST
def failed_messages() -> list[dict]:
	"""The customer messages the AI could not analyse (after every retry), newest first: their
	escalation, lead or tasks never happened until someone retries them."""
	require_manager()
	filters = {"classification_status": "Failed", "direction": "Incoming"}
	if (allowed := access.allowed_numbers()) is not None:
		filters["whatsapp_number"] = ["in", allowed or [""]]  # a manager given numbers: only theirs
	rows = frappe.get_all(
		"Periskope Message",
		filters=filters,
		fields=[
			"name",
			"periskope_chat",
			"chat_name",
			"contact_number",
			"body",
			"message_type",
			"classification_error",
			"message_timestamp",
		],
		order_by="message_timestamp desc",
		limit=50,
	)
	for r in rows:
		r.body = (r.body or "")[:160]
		r.classification_error = (r.classification_error or "")[:200]
	return rows


@frappe.whitelist(methods=["POST"])
def retry_failed_message(message: str) -> None:
	"""Analyse a failed message again; a voice note is transcribed again first."""
	require_manager()
	access.require_message(message)
	row = frappe.db.get_value(
		"Periskope Message", message, ["classification_status", "message_type", "transcript"], as_dict=True
	)
	if not row or row.classification_status != "Failed":
		frappe.throw(_("This message is not waiting for a retry."))
	if row.message_type == "audio" and not row.transcript:
		from periskope_connector.ai import transcribe

		frappe.db.set_value(
			"Periskope Message",
			message,
			{"transcript_attempts": 0, "classification_status": "Skipped", "classification_error": ""},
			update_modified=False,
		)
		transcribe.queue(message)
		return
	from periskope_connector.ai import classifier

	frappe.db.set_value(
		"Periskope Message",
		message,
		{"classification_status": "Pending", "classification_attempts": 0, "classification_error": ""},
		update_modified=False,
	)
	classifier.queue(message)


@frappe.whitelist()
def get_health() -> dict:
	"""The live figures for the Health tab of the settings page."""
	require_manager()
	access.require_every_number()
	from periskope_connector.health import get_health as figures

	return figures()


@frappe.whitelist()
def backfill_messages(days: int = 7) -> dict:
	"""Fetch the last `days` days of chat history from Periskope and save what webhooks missed."""
	require_admin()
	from periskope_connector.backfill import run

	return run(days=min(max(int(days), 1), 30))


@frappe.whitelist()
def get_flow() -> dict:
	"""The health and figures of each step, for the Flow tab of the settings page."""
	require_manager()
	access.require_every_number()
	from periskope_connector.flow import get_flow as figures

	return figures()


@frappe.whitelist()
def set_tag_active(tag: str, active: int = 1) -> None:
	"""Flow tab: switch a tag on or off."""
	require_admin()
	from periskope_connector.flow import set_tag_active as switch

	switch(tag, bool(int(active)))


@frappe.whitelist()
def set_flow_setting(field: str, value=None) -> None:
	"""Flow dialog: change one setting and save it."""
	require_admin()
	from periskope_connector.flow import set_setting

	set_setting(field, value)
