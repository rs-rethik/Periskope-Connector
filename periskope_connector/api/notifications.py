"""The inbox's notification bell: the user's notifications about WhatsApp chats (assignments,
mentions in notes, overdue replies), from Frappe's Notification Log."""

import frappe

from periskope_connector import access

LIMIT = 50


@frappe.whitelist()
def get_notifications() -> dict:
	access.require_inbox_user()
	rows = frappe.get_all(
		"Notification Log",
		filters={"for_user": frappe.session.user, "document_type": "Periskope Chat"},
		fields=["name", "subject", "type", "document_name", "from_user", "read", "creation"],
		order_by="creation desc",
		limit=LIMIT,
	)
	for row in rows:
		row.subject = frappe.utils.strip_html(row.subject or "")
		row.from_name = frappe.utils.get_fullname(row.from_user) if row.from_user else ""
	unread = frappe.db.count(
		"Notification Log", {"for_user": frappe.session.user, "document_type": "Periskope Chat", "read": 0}
	)
	return {"notifications": rows, "unread": unread}


@frappe.whitelist(methods=["POST"])
def mark_read(name: str | None = None) -> None:
	"""Mark one notification read, or all of the user's chat notifications."""
	access.require_inbox_user()
	filters = {"for_user": frappe.session.user, "document_type": "Periskope Chat", "read": 0}
	if name:
		filters["name"] = name
	for row in frappe.get_all("Notification Log", filters=filters, pluck="name"):
		frappe.db.set_value("Notification Log", row, "read", 1, update_modified=False)
