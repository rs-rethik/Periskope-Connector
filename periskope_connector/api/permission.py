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
	"""Show the WhatsApp inbox in the app list (and CRM's app switcher) to the users who can open
	it: sales users, sales managers and the admins."""
	from periskope_connector.access import can_use_inbox

	return can_use_inbox()
