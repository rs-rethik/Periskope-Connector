"""Who may do what. Every whitelisted method starts with one of these checks (a test makes sure):

- require_admin(): System Manager or WhatsApp Manager. Anything that changes how the app works
  (settings, tags, rules, the agent, sending tests).
- require_manager(): the admins and the Sales Managers listed in Periskope Settings (the Frappe
  role "Sales Manager" alone is not enough). Reading figures, working the contact queue, approving
  or rejecting the agent's suggestions.
- Sales users work only in the CRM; the app calls nothing on their behalf.
- The two guest endpoints authenticate themselves: the webhook by its signature, the suggestion
  page by its single-use token.
"""

import frappe
from frappe import _

ADMIN_ROLES = ("System Manager", "WhatsApp Manager")
# The roles that may open the inbox at all. A "Sales Manager" role holder works it as a sales user
# unless they are in Periskope Settings > Sales Managers.
INBOX_MANAGER_ROLES = (*ADMIN_ROLES, "Sales Manager")


def is_admin(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(ADMIN_ROLES) & set(frappe.get_roles(user)))


def sales_managers() -> set[str]:
	"""The users in Periskope Settings > Sales Managers."""
	return set(
		frappe.get_all(
			"Periskope Team Member",
			filters={"parenttype": "Periskope Settings", "parentfield": "sales_managers"},
			pluck="user",
		)
	)


def is_manager(user: str | None = None) -> bool:
	"""An admin, or a Sales Manager listed in Periskope Settings."""
	user = user or frappe.session.user
	return is_admin(user) or user in sales_managers()


def require_admin():
	frappe.only_for(ADMIN_ROLES)


def require_manager():
	if not is_manager():
		frappe.throw(_("Only the Sales Managers can do this."), frappe.PermissionError)


def has_app_permission() -> bool:
	"""Show the WhatsApp inbox in the app list (and CRM's app switcher) to the users who can open
	it: sales users, sales managers and the admins."""
	from periskope_connector.access import can_use_inbox

	return can_use_inbox()
