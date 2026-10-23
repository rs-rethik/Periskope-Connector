"""Live updates for the WhatsApp inbox over Frappe's socket.io (no polling).

When a chat or one of its messages changes, each user who may work that chat's number gets a small
"periskope_inbox" event with the chat's name and number only. The inbox then reloads what it shows
through the permitted API, so no message text goes out on the socket and a user who may not see the
chat reads nothing.
"""

import frappe

from periskope_connector.api.permission import MANAGER_ROLES

EVENT = "periskope_inbox"


def _recipients(number: str | None) -> list[str]:
	managers = frappe.get_all(
		"Has Role",
		filters={"role": ["in", MANAGER_ROLES], "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	agents = []
	if number:
		agents = frappe.get_all(
			"Periskope Agent Number",
			filters={"whatsapp_number": number, "parenttype": "Periskope Agent"},
			pluck="parent",
		)
		agents = frappe.get_all(
			"Periskope Agent", filters={"name": ["in", agents or [""]], "is_active": 1}, pluck="user"
		)
	enabled = set(
		frappe.get_all(
			"User", filters={"name": ["in", list({*managers, *agents}) or [""]], "enabled": 1}, pluck="name"
		)
	)
	return sorted(enabled - {"Guest"})


def chat_changed(chat: str, kind: str = "message"):
	"""Tell the users who may work this chat's number that it changed. Never raises."""
	try:
		number = frappe.db.get_value("Periskope Chat", chat, "whatsapp_number")
		for user in _recipients(number):
			frappe.publish_realtime(
				EVENT, {"chat": chat, "number": number, "kind": kind}, user=user, after_commit=True
			)
	except Exception:
		frappe.log_error(title="Periskope: could not send the inbox update")
