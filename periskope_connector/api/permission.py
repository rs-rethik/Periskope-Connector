"""Who may do what. Every whitelisted method starts with one of these checks (a test makes sure):

- require_admin(): System Manager or WhatsApp Manager. Anything that changes how the app works
  (settings, tags, rules, the agent, sending tests).
- require_manager(): the admins and Sales Manager. Reading figures, working the contact queue,
  approving or rejecting the agent's suggestions.
- Sales users work only in the CRM; the app calls nothing on their behalf.
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
