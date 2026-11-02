"""Territory sales reps for chats and groups.

A chat's customer belongs to a territory: its deal's, else its lead's, else its organization's (CRM
Organization.territory). The territory's manager (CRM Territory.territory_manager; the nearest parent
territory's manager when it has none) is the sales rep for that customer:

- a chat or group nobody is assigned to is assigned to its rep, when it is made and when it gets its
  CRM link (assign_rep). Hand assignments and team rules are never replaced;
- the rep's team are the sales persons under the rep's own Sales Person in ERPNext (Sales Person ->
  Employee -> user). The rep can hand a chat to them, and sees the chats assigned to them (access.py).

assign_unassigned() assigns the existing chats once, quietly (no bell or email for each).
"""

from contextlib import contextmanager

import frappe
from frappe import _


def manager_of(territory: str | None) -> str | None:
	"""The territory's manager (an enabled user), else the nearest parent territory's."""
	seen = set()
	while territory and territory not in seen and frappe.db.exists("DocType", "CRM Territory"):
		seen.add(territory)
		row = frappe.db.get_value(
			"CRM Territory", territory, ["territory_manager", "parent_crm_territory"], as_dict=True
		)
		if not row:
			return None
		if row.territory_manager and frappe.db.get_value("User", row.territory_manager, "enabled"):
			return row.territory_manager
		territory = row.parent_crm_territory
	return None


def territory_of(chat: str) -> str | None:
	"""The territory of a chat's customer: its deal's, its lead's, or its organization's."""
	row = frappe.db.get_value("Periskope Chat", chat, ["crm_deal", "crm_lead", "organization"], as_dict=True)
	if not row:
		return None
	organizations = [row.organization]
	for doctype, name in (("CRM Deal", row.crm_deal), ("CRM Lead", row.crm_lead)):
		if not name:
			continue
		found = frappe.db.get_value(doctype, name, ["territory", "organization"], as_dict=True)
		if found and found.territory:
			return found.territory
		if found:
			organizations.append(found.organization)
	for organization in organizations:
		territory = organization and frappe.db.get_value("CRM Organization", organization, "territory")
		if territory:
			return territory
	return None


def rep_for(chat: str) -> str | None:
	return manager_of(territory_of(chat))


def team_of(user: str | None) -> list[str]:
	"""The users of the sales persons under the user's own Sales Person (ERPNext), the user not
	included. Empty when the user has no Sales Person or ERPNext is not installed."""
	if not user or not frappe.db.exists("DocType", "Sales Person"):
		return []
	key = f"periskope_team_of:{user}"
	if key in frappe.local.cache:
		return frappe.local.cache[key]
	employees = frappe.get_all("Employee", filters={"user_id": user}, pluck="name")
	heads = frappe.get_all(
		"Sales Person", filters={"employee": ["in", employees or [""]], "enabled": 1}, pluck="name"
	)
	members = set()
	for head in heads:
		members.update(frappe.db.get_descendants("Sales Person", head) or [])
	member_employees = frappe.get_all(
		"Sales Person",
		filters={"name": ["in", list(members) or [""]], "enabled": 1, "employee": ["is", "set"]},
		pluck="employee",
	)
	users = frappe.get_all(
		"Employee",
		filters={"name": ["in", member_employees or [""]], "user_id": ["is", "set"]},
		pluck="user_id",
	)
	team = sorted(u for u in set(users) if u != user and frappe.db.get_value("User", u, "enabled"))
	frappe.local.cache[key] = team
	return team


def assign_rep(chat: str) -> str | None:
	"""Assign an unassigned chat to its territory's rep. Returns the rep, or None. Never raises."""
	try:
		if frappe.db.get_value("Periskope Chat", chat, "_assign") not in (None, "", "[]"):
			return None
		territory = territory_of(chat)
		rep = manager_of(territory)
		if not rep:
			return None
		from periskope_connector import workflow

		workflow.assign(chat, [rep])
		workflow.log(chat, _("Territory"), _("{0}: its sales rep").format(territory))
		return rep
	except Exception:
		frappe.log_error(title=f"Periskope: could not assign chat {chat} to its territory's rep")
		return None


@contextmanager
def _quietly():
	"""No bell or email for each assignment (a bulk run)."""
	from frappe.desk.form import assign_to

	original = assign_to.notify_assignment
	assign_to.notify_assignment = lambda *args, **kwargs: None
	try:
		yield
	finally:
		assign_to.notify_assignment = original


def assign_unassigned() -> dict:
	"""Assign every unassigned chat and group to its territory's rep, quietly. Run once after setting
	up the territories' managers:
	bench --site <site> execute periskope_connector.territory.assign_unassigned"""
	chats = frappe.get_all(
		"Periskope Chat",
		filters=[["_assign", "in", ["", "[]"]]],
		or_filters=[["crm_deal", "is", "set"], ["crm_lead", "is", "set"], ["organization", "is", "set"]],
		pluck="name",
	)
	chats += frappe.get_all(
		"Periskope Chat",
		filters=[["_assign", "is", "not set"]],
		or_filters=[["crm_deal", "is", "set"], ["crm_lead", "is", "set"], ["organization", "is", "set"]],
		pluck="name",
	)
	assigned, by_rep = 0, {}
	with _quietly():
		for chat in dict.fromkeys(chats):
			rep = assign_rep(chat)
			if rep:
				assigned += 1
				by_rep[rep] = by_rep.get(rep, 0) + 1
	frappe.db.commit()
	return {"checked": len(set(chats)), "assigned": assigned, "by_rep": by_rep}
