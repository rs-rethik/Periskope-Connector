"""Who may see which chats in the WhatsApp inbox. Enforced on the server: the permission query
conditions and has_permission hooks of Periskope Chat and Periskope Message (hooks.py) apply to every
list and read, and the inbox API only reads through them.

- A user given numbers (as a Periskope Agent, or through a Periskope Team they are in) sees every
  chat on those numbers, and no other number, whatever their role: a Sales Manager given one number
  works that number only. System Manager and WhatsApp Manager always see every number.
- A manager (Sales Manager) given no numbers sees every chat on every number.
- Anyone also sees the chats assigned to them (or to a sales person under them), on any number.
- Anyone else sees nothing.
"""

import frappe

from periskope_connector.api.permission import ADMIN_ROLES, INBOX_MANAGER_ROLES
from periskope_connector.api.permission import is_manager as _is_manager
from periskope_connector.periskope_connector.doctype.periskope_team.periskope_team import teams_of

INBOX_ROLES = (*INBOX_MANAGER_ROLES, "Sales User")


def assigned_like(user: str) -> str:
	"""A LIKE pattern for the user in a document's _assign (a JSON list of users): the quotes make it
	the whole email, and "_" / "%" in it are taken literally (in a LIKE they match any character)."""
	literal = (user or "").replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
	return f'%"{literal}"%'


def is_manager(user: str | None = None) -> bool:
	"""An admin, or a Sales Manager listed in Periskope Settings (permission.is_manager)."""
	return _is_manager(user)


def can_use_inbox(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Guest":
		return False
	return (
		user == "Administrator" or bool(set(INBOX_ROLES) & set(frappe.get_roles(user))) or _is_manager(user)
	)


def require_inbox_user():
	if not can_use_inbox():
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)


def allowed_numbers(user: str | None = None) -> list[str] | None:
	"""The numbers whose chats the user may work. None means every number: an admin, or a manager
	who was given no numbers. Numbers given to a user limit them whatever their role."""
	user = user or frappe.session.user
	if user == "Administrator" or set(ADMIN_ROLES) & set(frappe.get_roles(user)):
		return None
	found = _given_numbers(user)
	if not found and is_manager(user):
		return None
	return found


def sees_every_number(user: str | None = None) -> bool:
	return allowed_numbers(user) is None


def managers_by_number() -> dict[str, list[str] | None]:
	"""Every enabled manager (except Administrator) and their numbers (None: every number), for
	alerts: a manager hears only about the numbers they see."""
	users = frappe.get_all("User", filters={"enabled": 1, "user_type": "System User"}, pluck="name")
	return {u: allowed_numbers(u) for u in users if u != "Administrator" and is_manager(u)}


def managers_of(number: str | None, managers: dict[str, list[str] | None]) -> list[str]:
	"""The managers (from managers_by_number) who see this number."""
	return [u for u, numbers in managers.items() if numbers is None or number in numbers]


def users_on_numbers(numbers: list[str]) -> set[str]:
	"""The users given any of these numbers: active agents and the members of teams that have them."""
	if not numbers:
		return set()
	rows = frappe.get_all(
		"Periskope Agent Number",
		filters={
			"whatsapp_number": ["in", numbers],
			"parenttype": ["in", ["Periskope Agent", "Periskope Team"]],
		},
		fields=["parent", "parenttype"],
	)
	agents = [r.parent for r in rows if r.parenttype == "Periskope Agent"]
	teams = [r.parent for r in rows if r.parenttype == "Periskope Team"]
	users = set()
	if agents:
		users |= set(
			frappe.get_all("Periskope Agent", filters={"name": ["in", agents], "is_active": 1}, pluck="user")
		)
	if teams:
		users |= set(
			frappe.get_all(
				"Periskope Team Member",
				filters={"parent": ["in", teams], "parenttype": "Periskope Team"},
				pluck="user",
			)
		)
	return users - {None}


def require_every_number(user: str | None = None):
	"""App-wide pages (health, flow, system logs) are for those who see every number."""
	if allowed_numbers(user) is not None:
		frappe.throw(
			frappe._("This page covers every number: it is not shown to you."), frappe.PermissionError
		)


def require_message(message: str, user: str | None = None):
	"""Refuse a message whose chat the user may not see."""
	chat = frappe.db.get_value("Periskope Message", message, "periskope_chat")
	if not chat or not can_see_chat(chat, user):
		frappe.throw(frappe._("You cannot open this message."), frappe.PermissionError)


def chat_ids_on(numbers: list[str]) -> list[str]:
	"""The chat ids (customer or group) that have a chat row on any of these numbers."""
	if not numbers:
		return []
	return frappe.get_all(
		"Periskope Chat", filters={"whatsapp_number": ["in", numbers]}, pluck="chat_id", distinct=True
	)


def require_chat_id(chat_id: str | None, user: str | None = None):
	"""Refuse a customer or group the user has no chat with on their numbers."""
	allowed = allowed_numbers(user)
	if allowed is None:
		return
	if not chat_id or not frappe.db.exists(
		"Periskope Chat", {"chat_id": chat_id, "whatsapp_number": ["in", allowed or [""]]}
	):
		frappe.throw(frappe._("This chat is not on your numbers."), frappe.PermissionError)


def require_contact(contact: str, user: str | None = None):
	"""Refuse a contact-queue entry whose number has no chat on the user's numbers."""
	if allowed_numbers(user) is None:
		return
	phone = frappe.db.get_value("Periskope Contact", contact, "phone")
	require_chat_id(f"{phone}@c.us" if phone else None, user)


def teams_on(numbers: list[str]) -> list[str]:
	"""The teams that have any of these numbers."""
	if not numbers:
		return []
	return frappe.get_all(
		"Periskope Agent Number",
		filters={"whatsapp_number": ["in", numbers], "parenttype": "Periskope Team"},
		pluck="parent",
		distinct=True,
	)


def number_filter(field: str = "whatsapp_number", user: str | None = None) -> dict:
	"""A get_all filter keeping rows to the user's numbers ({} when they see every number)."""
	allowed = allowed_numbers(user)
	return {} if allowed is None else {field: ["in", allowed or [""]]}


def chat_id_filter(field: str = "chat_id", user: str | None = None) -> dict:
	"""A get_all filter keeping rows to the chats on the user's numbers ({} for every number)."""
	allowed = allowed_numbers(user)
	return {} if allowed is None else {field: ["in", chat_ids_on(allowed) or [""]]}


def leads_on_my_numbers(user: str | None = None) -> list[str] | None:
	"""The leads of the chats on the user's numbers, or None when they see every number."""
	allowed = allowed_numbers(user)
	if allowed is None:
		return None
	return frappe.get_all(
		"Periskope Chat",
		filters={"whatsapp_number": ["in", allowed or [""]], "crm_lead": ["is", "set"]},
		pluck="crm_lead",
		distinct=True,
	)


def require_number(number: str | None, user: str | None = None):
	"""Refuse a number the user was not given (no number, or every number allowed: nothing to check)."""
	allowed = allowed_numbers(user)
	if number and allowed is not None and number not in allowed:
		frappe.throw(frappe._("You do not work the number {0}.").format(number), frappe.PermissionError)


def _given_numbers(user: str) -> list[str]:
	"""The numbers of the user's active Periskope Agent and of the teams they are in."""
	agent = frappe.db.get_value("Periskope Agent", {"user": user, "is_active": 1})
	parents = [("Periskope Agent", agent)] if agent else []
	parents += [("Periskope Team", t.name) for t in teams_of(user)]
	found = []
	for parenttype, parent in parents:
		found += frappe.get_all(
			"Periskope Agent Number",
			filters={"parent": parent, "parenttype": parenttype},
			pluck="whatsapp_number",
		)
	return sorted(set(found))


def chat_condition(user: str | None = None, table: str = "`tabPeriskope Chat`") -> str:
	"""SQL that limits Periskope Chat rows to those the user may see ("" for all): every chat on
	their numbers, and the chats assigned to them (or to their sales team, territory.py) on any
	number."""
	user = user or frappe.session.user
	numbers = allowed_numbers(user)
	if numbers is None:
		return ""
	from periskope_connector.territory import team_of

	# Assigned to the user, or (a territory's sales rep) to a sales person under them: on any number.
	assigned = " or ".join(
		f"{table}._assign like {frappe.db.escape(assigned_like(u))}" for u in [user, *team_of(user)]
	)
	if not numbers:
		return f"({assigned})"
	listed = ", ".join(frappe.db.escape(n) for n in numbers)
	return f"({assigned} or {table}.whatsapp_number in ({listed}))"


def message_condition(user: str | None = None) -> str:
	"""SQL that limits Periskope Message rows to the messages of chats the user may see."""
	condition = chat_condition(user, "c")
	if not condition:
		return ""
	if condition == "1=0":
		return condition
	return f"`tabPeriskope Message`.periskope_chat in (select c.name from `tabPeriskope Chat` c where {condition})"


def can_see_chat(chat: str, user: str | None = None) -> bool:
	user = user or frappe.session.user
	condition = chat_condition(user)
	if not condition:
		return bool(frappe.db.exists("Periskope Chat", chat))
	return bool(
		frappe.db.sql(
			f"select name from `tabPeriskope Chat` where name = %s and {condition}",
			(chat,),
		)
	)


# --- Hooks --------------------------------------------------------------------------------------


def chat_query(user: str | None = None) -> str:
	return chat_condition(user)


def message_query(user: str | None = None) -> str:
	return message_condition(user)


def chat_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if is_manager(user) and sees_every_number(user):
		return True
	if ptype not in (None, "read", "print"):
		return False  # sales users change chats only through the inbox API
	return can_see_chat(doc.name, user)


def message_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if is_manager(user) and sees_every_number(user):
		return True
	if ptype not in (None, "read", "print"):
		return False
	return bool(doc.periskope_chat) and can_see_chat(doc.periskope_chat, user)
