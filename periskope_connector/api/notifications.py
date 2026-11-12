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


# --- Notifications on the user's devices (push.py) ------------------------------------------------


@frappe.whitelist(methods=["GET", "POST"])
def push_config() -> dict:
	"""What the browser needs to turn device notifications on, and the user's own choices."""
	access.require_inbox_user()
	from periskope_connector import push

	if not push.available():
		return {"available": False}
	prefs = push.preferences(frappe.session.user)
	return {
		"available": True,
		"public_key": push.public_key(),
		"push_off": int(prefs.push_off or 0),
		"quiet_from": str(prefs.quiet_from or "")[:5],
		"quiet_to": str(prefs.quiet_to or "")[:5],
	}


@frappe.whitelist(methods=["POST"])
def push_subscribe(subscription: str | dict, user_agent: str = "") -> None:
	"""This browser wants the user's notifications."""
	access.require_inbox_user()
	from periskope_connector import push

	sub = frappe.parse_json(subscription) if isinstance(subscription, str) else subscription
	push.subscribe(frappe.session.user, sub or {}, user_agent)


@frappe.whitelist(methods=["POST"])
def push_unsubscribe(endpoint: str) -> None:
	access.require_inbox_user()
	from periskope_connector import push

	push.unsubscribe(frappe.session.user, endpoint)


@frappe.whitelist(methods=["POST"])
def save_preferences(push_off: int | str = 0, quiet_from: str = "", quiet_to: str = "") -> None:
	"""The user's own device-notification choices: off, or quiet between two times."""
	access.require_inbox_user()
	user = frappe.session.user
	values = {
		"push_off": 1 if frappe.utils.cint(push_off) else 0,
		"quiet_from": quiet_from or None,
		"quiet_to": quiet_to or None,
	}
	if frappe.db.exists("Periskope Notification Setting", user):
		frappe.db.set_value("Periskope Notification Setting", user, values)
	else:
		# The user's own settings row: a system record they change only through this call.
		frappe.get_doc({"doctype": "Periskope Notification Setting", "user": user, **values}).insert(
			ignore_permissions=True
		)
