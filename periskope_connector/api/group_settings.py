"""A WhatsApp group's settings from the inbox (Periskope: GET /chats/{id}, POST /chats/{id}/settings,
/add, /remove, /promote, /demote, /invite).

Anyone who may open the group sees its name, description, settings, members and invite link (the link
only when our number is a group admin). Changes are for managers, and go through our number, which
must be an admin of the group for most of them. Every change is seen by the members (WhatsApp posts a
notice in the group), so test mode allows changes only to a group whose members are all test numbers,
and adds only test numbers.
"""

import frappe
from frappe import _

from periskope_connector import access, safety
from periskope_connector.api.permission import require_manager
from periskope_connector.client import request

EPHEMERAL = (0, 86400, 604800, 7776000)
ACTIONS = ("add", "remove", "promote", "demote")
MAX_PEOPLE = 50


def _digits(value: str | None) -> str:
	return "".join(ch for ch in (value or "").split("@")[0] if ch.isdigit())


def _person(value) -> str:
	"""A member's number with its country code (a local number gets the company's, as in New chat)."""
	from periskope_connector.linking import with_country_code

	digits = _digits(str(value or ""))
	return with_country_code(digits) if len(digits) >= 10 else digits


def _group(chat: str) -> frappe._dict:
	access.require_inbox_user()
	if not access.can_see_chat(chat):
		frappe.throw(_("You cannot open this chat."), frappe.PermissionError)
	row = frappe.db.get_value(
		"Periskope Chat", chat, ["name", "chat_id", "is_group", "whatsapp_number", "org_phone"], as_dict=True
	)
	if not row or not (row.is_group or (row.chat_id or "").endswith("@g.us")):
		frappe.throw(_("This chat is not a WhatsApp group."))
	row.number = row.whatsapp_number or _digits(row.org_phone)
	from periskope_connector import meta

	if meta.is_meta(row.number):
		frappe.throw(_("Groups are only on the Periskope numbers."))
	return row


def _manager():
	require_manager()
	if not access.is_manager():
		frappe.throw(_("Only managers can change a group's settings."), frappe.PermissionError)


def _read(row) -> dict:
	data = request("GET", f"chats/{row.chat_id}", phone=row.number) or {}
	return data.get("chat", data) if isinstance(data, dict) else {}


def _members(data: dict) -> list[dict]:
	members = data.get("members") or {}
	items = members.values() if isinstance(members, dict) else members
	out = []
	for m in items:
		number = _digits(m.get("contact_id"))
		if number:
			out.append(
				{
					"number": number,
					"name": m.get("contact_name") or "",
					"is_admin": bool(m.get("is_admin") or m.get("is_super_admin")),
					"is_super_admin": bool(m.get("is_super_admin")),
				}
			)
	return sorted(out, key=lambda m: (not m["is_super_admin"], not m["is_admin"], m["name"] or m["number"]))


def _test_mode_block(row, members: list[dict]) -> str:
	"""Why test mode stops changes to this group, or ""."""
	if not safety.test_mode():
		return ""
	if not members:
		# Periskope gave no member list: nobody can say the group holds only test numbers.
		return _("Test mode is on and this group's members could not be read, so it cannot be changed.")
	allowed = safety.allowed_numbers()
	others = [
		m["number"] for m in members if m["number"] != _digits(row.number) and m["number"] not in allowed
	]
	if others:
		return _(
			"Test mode is on and this group has members who are not test numbers, so it cannot be changed."
		)
	return ""


def _info(row, data: dict) -> dict:
	members = _members(data)
	ours = next((m for m in members if m["number"] == _digits(row.number)), None)
	blocked = _test_mode_block(row, members)
	return {
		"name": data.get("chat_name") or "",
		"description": data.get("group_description") or "",
		"image": data.get("chat_image") or "",
		"member_count": data.get("member_count") or len(members),
		"info_admins_only": bool(data.get("info_admins_only")),
		"messages_admins_only": bool(data.get("messages_admins_only")),
		"add_members_admins_only": bool(data.get("add_members_admins_only")),
		"invite_link": data.get("invite_link") or "",
		"members": members,
		"we_are_admin": bool(ours and ours["is_admin"]),
		"can_change": access.is_manager() and not blocked,
		"blocked": blocked,
	}


def _guard(row, add: list[str] | None = None) -> None:
	"""Managers only; in test mode only a group of test numbers, and only test numbers added."""
	_manager()
	blocked = _test_mode_block(row, _members(_read(row)))
	if blocked:
		frappe.throw(blocked)
	if add and safety.test_mode():
		allowed = safety.allowed_numbers()
		outside = [n for n in add if n not in allowed]
		if outside:
			frappe.throw(
				_("Test mode is on: only test numbers can be added ({0}).").format(", ".join(outside))
			)


def _rename(row, name: str):
	"""The new group name on the app's chat rows and group record."""
	frappe.db.set_value("Periskope Chat", {"chat_id": row.chat_id}, "chat_name", name, update_modified=False)
	if frappe.db.exists("Periskope Group", {"chat_id": row.chat_id}):
		frappe.db.set_value("Periskope Group", {"chat_id": row.chat_id}, "group_name", name)


@frappe.whitelist()
def info(chat: str) -> dict:
	"""The group's name, description, settings, members and invite link, read from Periskope now."""
	access.require_inbox_user()
	row = _group(chat)
	return _info(row, _read(row))


@frappe.whitelist(methods=["POST"])
def update(
	chat: str,
	name: str | None = None,
	description: str | None = None,
	info_admins_only: int | str | None = None,
	messages_admins_only: int | str | None = None,
	add_members_admins_only: int | str | None = None,
	join_approval: int | str | None = None,
	ephemeral: int | str | None = None,
) -> dict:
	"""Change the group's name, description or settings (managers)."""
	access.require_inbox_user()
	row = _group(chat)
	_guard(row)
	body = {}
	if name is not None:
		name = name.strip()
		if not name or len(name) > 100:
			frappe.throw(_("The group name must be 1 to 100 characters."))
		body["name"] = name
	if description is not None:
		if len(description) > 2048:
			frappe.throw(_("The description is longer than 2048 characters."))
		body["description"] = description
	for field, key in (
		(info_admins_only, "infoAdminsOnly"),
		(messages_admins_only, "messagesAdminsOnly"),
		(add_members_admins_only, "addMembersAdminsOnly"),
		(join_approval, "joinApprovalAdminsOnly"),
	):
		if field is not None and field != "":
			body[key] = bool(frappe.utils.cint(field))
	if ephemeral is not None and ephemeral != "":
		seconds = frappe.utils.cint(ephemeral)
		if seconds not in EPHEMERAL:
			frappe.throw(_("Disappearing messages can be off, 24 hours, 7 days or 90 days."))
		body["ephemeral"] = seconds
	if not body:
		frappe.throw(_("Nothing to change."))
	data = request("POST", f"chats/{row.chat_id}/settings", phone=row.number, json=body) or {}
	if body.get("name"):
		_rename(row, body["name"])
	return _info(row, data if data.get("chat_id") else _read(row))


@frappe.whitelist(methods=["POST"])
def members(chat: str, action: str, numbers: str | list) -> dict:
	"""Add, remove, promote or demote members (managers): {results, info}. results has, per number,
	whether it worked and Periskope's message (an invite is sent to whom WhatsApp will not add)."""
	access.require_inbox_user()
	row = _group(chat)
	if action not in ACTIONS:
		frappe.throw(_("Unknown action."))
	numbers = frappe.parse_json(numbers) if isinstance(numbers, str) else numbers
	numbers = list(dict.fromkeys(_person(n) for n in (numbers or []) if _digits(str(n))))
	if not numbers or len(numbers) > MAX_PEOPLE:
		frappe.throw(_("Give 1 to {0} numbers, with the country code.").format(MAX_PEOPLE))
	if any(len(n) < 10 for n in numbers):
		frappe.throw(_("Write each number with its country code, for example 919876543210."))
	if action == "remove" and _digits(row.number) in numbers:
		frappe.throw(_("Our own number cannot be removed from here."))
	_guard(row, add=numbers if action == "add" else None)
	result = (
		request("POST", f"chats/{row.chat_id}/{action}", phone=row.number, json={"participants": numbers})
		or {}
	)
	results = []
	for number in numbers:
		r = result.get(f"{number}@c.us") or result.get(number) or {}
		results.append(
			{
				"number": number,
				"ok": bool(r.get("is_success", True)),
				"invited": bool(r.get("isInviteV4Sent")),
				"message": r.get("message") or "",
			}
		)
	return {"results": results, "info": _info(row, _read(row))}


@frappe.whitelist(methods=["POST"])
def reset_invite(chat: str) -> dict:
	"""A new invite link (the old one stops working)."""
	access.require_inbox_user()
	row = _group(chat)
	_guard(row)
	data = request("POST", f"chats/{row.chat_id}/invite", phone=row.number) or {}
	return {"invite_link": data.get("invite_link") or ""}


@frappe.whitelist(methods=["POST"])
def create(
	number: str,
	name: str,
	participants: str | list,
	description: str = "",
	messages_admins_only: int | str = 0,
	info_admins_only: int | str = 1,
	add_members_admins_only: int | str = 1,
) -> dict:
	"""Make a WhatsApp group on one of our Periskope numbers (managers): {chat}, the new group's chat. Our number is
	its admin. Who WhatsApp will not add directly gets an invite. In test mode only test numbers."""
	access.require_inbox_user()
	_manager()
	from periskope_connector import meta

	number = _digits(number)
	if not frappe.db.get_value("WhatsApp Number", number, "is_active") or meta.is_meta(number):
		frappe.throw(_("Choose an active Periskope number to make the group on."))
	name = (name or "").strip()
	if not name or len(name) > 100:
		frappe.throw(_("The group name must be 1 to 100 characters."))
	people = frappe.parse_json(participants) if isinstance(participants, str) else participants
	people = list(dict.fromkeys(_person(p) for p in (people or []) if _digits(str(p))))
	people = [p for p in people if p != number]
	if not people or len(people) > MAX_PEOPLE:
		frappe.throw(_("Add 1 to {0} members, with the country code.").format(MAX_PEOPLE))
	if any(len(p) < 10 for p in people):
		frappe.throw(_("Write each number with its country code, for example 919876543210."))
	if safety.test_mode():
		allowed = safety.allowed_numbers()
		outside = [p for p in people if p not in allowed]
		if outside:
			frappe.throw(
				_("Test mode is on: a group can only have test numbers ({0}).").format(", ".join(outside))
			)
	options = {
		"messagesAdminsOnly": bool(frappe.utils.cint(messages_admins_only)),
		"infoAdminsOnly": bool(frappe.utils.cint(info_admins_only)),
		"addMembersAdminsOnly": bool(frappe.utils.cint(add_members_admins_only)),
	}
	if (description or "").strip():
		options["description"] = description.strip()[:2048]
	made = (
		request(
			"POST",
			"chats/create",
			phone=number,
			json={"group_name": name, "participants": people, "options": options},
		)
		or {}
	)
	made = made.get("chat", made) if isinstance(made, dict) else {}
	chat_id = made.get("chat_id")
	if not chat_id:
		frappe.throw(_("Periskope did not make the group."))
	return {"chat": _chat_row(number, chat_id, name, _("Group created"))}


def _chat_row(number: str, chat_id: str, name: str, preview: str) -> str:
	"""The app's chat for a group our number just made or joined, so it opens in the inbox at once."""
	from periskope_connector import chat as chat_module

	row = chat_module.name_of(number, chat_id)
	if row:
		frappe.db.set_value("Periskope Chat", row, "status", "Open", update_modified=False)
		return row
	now = frappe.utils.now_datetime()
	row = chat_module._insert_chat(
		{
			"chat_id": chat_id,
			"is_group": 1,
			"chat_name": name,
			"org_phone": number,
			"whatsapp_number": number,
			"status": "Open",
			"last_message_at": now,
			"last_message_preview": preview,
			"last_message_direction": "Outgoing",
		}
	)
	chat_module._lifecycle("new_chat", row, now)
	return row


def _invite_code(link: str) -> str:
	"""The code at the end of a WhatsApp invite link (https://chat.whatsapp.com/[invite/]<code>)."""
	code = (link or "").strip().rstrip("/").split("?")[0].split("/")[-1]
	if not code or not all(ch.isalnum() or ch in "-_" for ch in code) or len(code) > 64:
		frappe.throw(_("Paste a WhatsApp group invite link, like https://chat.whatsapp.com/AbC123..."))
	return code


@frappe.whitelist(methods=["POST"])
def join(number: str, link: str) -> dict:
	"""Our number joins a group by its invite link (managers): {chat}, the group's chat in the inbox.
	The members see that our number joined; nothing else is sent. Not in test mode: who is in the
	group cannot be known before joining it, and its members would see our number arrive."""
	access.require_inbox_user()
	_manager()
	if safety.test_mode():
		frappe.throw(_("Test mode is on: our number cannot join a group."))
	from periskope_connector import meta

	number = _digits(number)
	access.require_number(number)
	if not frappe.db.get_value("WhatsApp Number", number, "is_active") or meta.is_meta(number):
		frappe.throw(_("Choose an active Periskope number to join with."))
	joined = (
		request("POST", "chats/accept-invite", phone=number, json={"invite_code": _invite_code(link)}) or {}
	)
	joined = joined.get("chat", joined) if isinstance(joined, dict) else {}
	chat_id = joined.get("chat_id")
	if not chat_id:
		frappe.throw(_("Periskope did not join the group. The link may have been reset or expired."))
	return {"chat": _chat_row(number, chat_id, joined.get("chat_name") or chat_id, _("Joined the group"))}


@frappe.whitelist(methods=["POST"])
def leave(chat: str) -> dict:
	"""Our number leaves the group (managers). The app keeps the chat and its history, closed; new
	messages stop. In test mode only a group of test numbers (the members see that we left)."""
	access.require_inbox_user()
	row = _group(chat)
	_guard(row)
	request("POST", f"chats/{row.chat_id}/leave", phone=row.number)
	frappe.db.set_value("Periskope Chat", chat, "status", "Closed", update_modified=False)
	from periskope_connector import workflow

	workflow.log(chat, _("Group"), _("Our number left the group ({0})").format(frappe.session.user))
	return {"left": True}
