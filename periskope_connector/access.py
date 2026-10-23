"""Who may see which chats in the WhatsApp inbox. Enforced on the server: the permission query
conditions and has_permission hooks of Periskope Chat and Periskope Message (hooks.py) apply to every
list and read, and the inbox API only reads through them.

- Managers (System Manager, WhatsApp Manager, Sales Manager) see every chat on every number.
- A sales user sees a chat only when it is on one of their numbers (as a Periskope Agent, or
  through a Periskope Team they are in) AND
  - it is assigned to them, or
  - it belongs to one of their teams whose members see the team's chats, or
  - its lead or deal is theirs (lead owner / deal owner).
- Anyone else sees nothing.
"""

import frappe

from periskope_connector.api.permission import MANAGER_ROLES
from periskope_connector.periskope_connector.doctype.periskope_team.periskope_team import teams_of

INBOX_ROLES = (*MANAGER_ROLES, "Sales User")


def is_manager(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(MANAGER_ROLES) & set(frappe.get_roles(user)))


def can_use_inbox(user: str | None = None) -> bool:
	user = user or frappe.session.user
	if user == "Guest":
		return False
	return user == "Administrator" or bool(set(INBOX_ROLES) & set(frappe.get_roles(user)))


def require_inbox_user():
	if not can_use_inbox():
		frappe.throw(frappe._("Not permitted"), frappe.PermissionError)


def allowed_numbers(user: str | None = None) -> list[str] | None:
	"""The numbers whose chats the user may work. None means every number (a manager)."""
	user = user or frappe.session.user
	if is_manager(user):
		return None
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


def _owned(user: str, doctype: str, field: str) -> str:
	return f"select name from `tab{doctype}` where `{field}` = {frappe.db.escape(user)}"


def chat_condition(user: str | None = None, table: str = "`tabPeriskope Chat`") -> str:
	"""SQL that limits Periskope Chat rows to those the user may see ("" for all)."""
	user = user or frappe.session.user
	numbers = allowed_numbers(user)
	if numbers is None:
		return ""
	if not numbers:
		return "1=0"
	listed = ", ".join(frappe.db.escape(n) for n in numbers)
	assigned = frappe.db.escape(f'%"{user}"%')
	open_teams = [t.name for t in teams_of(user) if t.team_can_see_team_chats]
	team = f" or {table}.team in ({', '.join(frappe.db.escape(t) for t in open_teams)})" if open_teams else ""
	return (
		f"({table}.whatsapp_number in ({listed}) and ("
		f"{table}._assign like {assigned}"
		f"{team}"
		f" or {table}.crm_lead in ({_owned(user, 'CRM Lead', 'lead_owner')})"
		f" or {table}.crm_deal in ({_owned(user, 'CRM Deal', 'deal_owner')})"
		"))"
	)


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
	if is_manager(user):
		return True
	if ptype not in (None, "read", "print"):
		return False  # sales users change chats only through the inbox API
	return can_see_chat(doc.name, user)


def message_permission(doc, ptype: str | None = None, user: str | None = None) -> bool:
	user = user or frappe.session.user
	if is_manager(user):
		return True
	if ptype not in (None, "read", "print"):
		return False
	return bool(doc.periskope_chat) and can_see_chat(doc.periskope_chat, user)
