import frappe

ALLOWED_ROLES = {"System Manager", "Sales Manager"}
MANAGER_ROLES = {"System Manager", "Sales Manager", "WhatsApp Manager"}


def has_app_permission() -> bool:
	"""Show Periskope Connector in the app list only to users who can open its pages."""
	if frappe.session.user == "Guest":
		return False
	return bool(ALLOWED_ROLES & set(frappe.get_roles()))


def message_has_permission(user=None, doc=None, ptype="read") -> bool | None:
	"""Periskope Message permission hook.

	Managers are decided by the doctype's own role permissions (return None). When "Reps see
	their own numbers" is on, a rep may read the messages of the company numbers assigned to
	them in the phones table — nothing else, and never write."""
	if frappe.session.user in ("Administrator", "Guest"):
		return None
	if MANAGER_ROLES & set(frappe.get_roles()):
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
