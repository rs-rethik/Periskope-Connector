"""What a rep or manager does with chats in the WhatsApp inbox, one chat or many at once (the list
view's bulk actions): status, assignment, team, labels, read marks, and the activity log.

Every chat must be one the user may see (access.py). Sales users assign to themselves or to whoever
works that chat's number; moving a chat to another team is for managers.
"""

import json

import frappe
from frappe import _

from periskope_connector import access, workflow
from periskope_connector.inbox_realtime import _recipients, chat_changed

MAX_BULK = 200


def _chats(chats) -> list[str]:
	access.require_inbox_user()
	if isinstance(chats, str):
		chats = json.loads(chats) if chats.strip().startswith("[") else [chats]
	chats = list(dict.fromkeys(chats or []))
	if not chats:
		frappe.throw(_("Choose at least one chat."))
	if len(chats) > MAX_BULK:
		frappe.throw(_("Choose at most {0} chats at once.").format(MAX_BULK))
	for chat in chats:
		if not access.can_see_chat(chat):
			frappe.throw(_("You cannot change one of these chats."), frappe.PermissionError)
	return chats


def _done(chats: list[str]) -> dict:
	frappe.db.commit()
	for chat in chats:
		chat_changed(chat, kind="update")
	return {"updated": len(chats)}


@frappe.whitelist(methods=["POST"])
def set_status(chats, status: str) -> dict:
	access.require_inbox_user()
	chats = _chats(chats)
	for chat in chats:
		workflow.set_status(chat, status, user=frappe.session.user)
	return _done(chats)


def assignable(chat: str) -> list[str]:
	"""Who can take a chat: whoever may work its number (managers, its agents and team members)."""
	number = frappe.db.get_value("Periskope Chat", chat, "whatsapp_number")
	return _recipients(number)


@frappe.whitelist()
def assignable_users(chat: str) -> list[dict]:
	access.require_inbox_user()
	_chats([chat])
	users = assignable(chat)
	return frappe.get_all(
		"User",
		filters={"name": ["in", users or [""]], "enabled": 1},
		fields=["name", "full_name", "user_image"],
		order_by="full_name asc",
	)


@frappe.whitelist(methods=["POST"])
def assign(chats, users) -> dict:
	"""Assign chats to users. A sales user may assign only to someone who can work the chat's number."""
	access.require_inbox_user()
	chats = _chats(chats)
	users = (
		json.loads(users)
		if isinstance(users, str) and users.strip().startswith("[")
		else ([users] if isinstance(users, str) else list(users or []))
	)
	for chat in chats:
		allowed = set(assignable(chat))
		wrong = [u for u in users if u not in allowed]
		if wrong:
			frappe.throw(
				_("{0} cannot work this chat's number.").format(", ".join(wrong)), frappe.PermissionError
			)
		workflow.assign(chat, users, by=frappe.session.user)
	return _done(chats)


@frappe.whitelist(methods=["POST"])
def assign_to_me(chats) -> dict:
	access.require_inbox_user()
	return assign(chats, [frappe.session.user])


@frappe.whitelist(methods=["POST"])
def unassign(chat: str, user: str) -> dict:
	"""Take someone off a chat. Sales users may take only themselves off; managers anyone."""
	access.require_inbox_user()
	chats = _chats([chat])
	if user != frappe.session.user and not access.is_manager():
		frappe.throw(_("Only a manager can take someone else off a chat."), frappe.PermissionError)
	workflow.unassign(chat, user, by=frappe.session.user)
	return _done(chats)


@frappe.whitelist(methods=["POST"])
def set_team(chats, team: str | None = None) -> dict:
	access.require_inbox_user()
	if not access.is_manager():
		frappe.throw(_("Only a manager can move chats to another team."), frappe.PermissionError)
	chats = _chats(chats)
	if team and not frappe.db.exists("Periskope Team", team):
		frappe.throw(_("Unknown team {0}").format(team))
	for chat in chats:
		workflow.set_team(chat, team or None, user=frappe.session.user)
	return _done(chats)


@frappe.whitelist(methods=["POST"])
def add_label(chats, tag: str) -> dict:
	access.require_inbox_user()
	chats = _chats(chats)
	if not frappe.db.exists("Periskope Tag", tag):
		frappe.throw(_("Unknown label {0}").format(tag))
	for chat in chats:
		if frappe.db.exists(
			"Periskope Chat Label", {"parent": chat, "parenttype": "Periskope Chat", "tag": tag}
		):
			continue
		doc = frappe.get_doc("Periskope Chat", chat)
		doc.append("labels", {"tag": tag})
		doc.flags.ignore_permissions = True
		doc.save()
		workflow.log(chat, "Label", _("Added {0}").format(tag), user=frappe.session.user)
	return _done(chats)


@frappe.whitelist(methods=["POST"])
def remove_label(chats, tag: str) -> dict:
	access.require_inbox_user()
	chats = _chats(chats)
	for chat in chats:
		doc = frappe.get_doc("Periskope Chat", chat)
		kept = [row for row in doc.labels if row.tag != tag]
		if len(kept) == len(doc.labels):
			continue
		doc.set("labels", kept)
		doc.flags.ignore_permissions = True
		doc.save()
		workflow.log(chat, "Label", _("Removed {0}").format(tag), user=frappe.session.user)
	return _done(chats)


@frappe.whitelist(methods=["POST"])
def mark_read(chats) -> dict:
	access.require_inbox_user()
	from periskope_connector.api.inbox import mark_seen

	chats = _chats(chats)
	for chat in chats:
		mark_seen(chat)
	frappe.db.commit()
	return {"updated": len(chats)}


@frappe.whitelist()
def teams() -> list[dict]:
	"""The teams, for the team filter and the team picker."""
	access.require_inbox_user()
	return frappe.get_all("Periskope Team", fields=["name", "assignment"], order_by="name asc")


@frappe.whitelist()
def activity(chat: str) -> list[dict]:
	"""The chat's activity log, newest first."""
	access.require_inbox_user()
	_chats([chat])
	rows = frappe.get_all(
		"Periskope Chat Activity",
		filters={"chat": chat},
		fields=["name", "action", "details", "owner", "creation"],
		order_by="creation desc",
		limit=200,
	)
	for row in rows:
		row.owner_name = frappe.utils.get_fullname(row.owner)
	return rows
