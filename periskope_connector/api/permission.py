"""Who may do what. Every whitelisted method starts with one of these checks (a test makes sure):

- require_admin(): System Manager or WhatsApp Manager. Anything that changes how the app works
  (settings, tags, rules, the agent, sending tests).
- require_manager(): the admins and Sales Manager. Reading figures, working the contact queue,
  approving or rejecting the agent's suggestions.
- Sales users work only in the CRM; their endpoints (api/crm.py) check access to the lead or deal.
- The two guest endpoints authenticate themselves: the webhook by its signature, the suggestion
  page by its single-use token.
"""

import frappe

ADMIN_ROLES = ("System Manager", "WhatsApp Manager")
MANAGER_ROLES = (*ADMIN_ROLES, "Sales Manager")


def require_admin():
	frappe.only_for(ADMIN_ROLES)


def require_manager():
	frappe.only_for(MANAGER_ROLES)


def has_app_permission() -> bool:
	"""Show Periskope Connector in the app list only to users who can open its pages."""
	if frappe.session.user == "Guest":
		return False
	return bool(set(MANAGER_ROLES) & set(frappe.get_roles()))


def message_has_permission(user=None, doc=None, ptype="read") -> bool | None:
	"""Periskope Message permission hook.

	Managers are decided by the doctype's own role permissions (return None). When "Reps see
	their own numbers" is on, a rep may read the messages of the company numbers assigned to
	them in the phones table — nothing else, and never write."""
	if frappe.session.user in ("Administrator", "Guest"):
		return None
	if set(MANAGER_ROLES) & set(frappe.get_roles()):
		return None
	if ptype != "read" or not frappe.db.get_single_value("Periskope Settings", "reps_see_own_numbers"):
		return None
	if doc is None:
		return None  # list access for non-managers stays closed (the list route filters anyway)

	numbers = frappe.cache().get_value("periskope_rep_numbers:" + frappe.session.user)
	if numbers is None:
		numbers = frappe.get_all(
			"Periskope Phone",
			filters={"parent": "Periskope Settings", "rep": frappe.session.user},
			pluck="number",
		)
		frappe.cache().set_value("periskope_rep_numbers:" + frappe.session.user, numbers)
	return bool(numbers and doc.org_phone in numbers)
