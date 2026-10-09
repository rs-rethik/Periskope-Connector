import frappe

from periskope_connector.tags import default_actions


def execute():
	"""Give the existing tags the actions the app used to do in code (alert on High severity,
	timeline comments, lead from an enquiry), so nothing changes until the team edits them.
	Skipped when any action exists, so a team that has cleared them is not overridden."""
	if frappe.db.count("Periskope Tag Action"):
		return
	for name in frappe.get_all("Periskope Tag", pluck="name"):
		tag = frappe.get_doc("Periskope Tag", name)
		for row in default_actions(name):
			tag.append("actions", row)
		tag.flags.ignore_permissions = True
		tag.save()
