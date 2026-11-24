"""Live updates for the WhatsApp inbox over Frappe's socket.io (no polling).

When a chat or one of its messages changes, each user who may work that chat's number gets a small
"periskope_inbox" event with the chat's name and number only. The inbox then reloads what it shows
through the permitted API, so no message text goes out on the socket and a user who may not see the
chat reads nothing.
"""

import frappe

from periskope_connector.api.permission import ADMIN_ROLES, sales_managers

EVENT = "periskope_inbox"


def _recipients(number: str | None) -> list[str]:
	"""Who sees the number: its agents and team members, the admins, and the Sales Managers who were
	given no numbers (they see every number, access.allowed_numbers)."""
	from periskope_connector.access import _given_numbers, users_on_numbers

	admins = frappe.get_all(
		"Has Role",
		filters={"role": ["in", ADMIN_ROLES], "parenttype": "User"},
		pluck="parent",
		distinct=True,
	)
	managers = [*admins, *(u for u in sales_managers() if not _given_numbers(u))]
	agents = users_on_numbers([number]) if number else set()
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
