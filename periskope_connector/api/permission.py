import frappe

ALLOWED_ROLES = {"System Manager", "Sales Manager"}


def has_app_permission() -> bool:
	"""Show Periskope Connector in the app list only to users who can open its pages."""
	if frappe.session.user == "Guest":
		return False
	return bool(ALLOWED_ROLES & set(frappe.get_roles()))
