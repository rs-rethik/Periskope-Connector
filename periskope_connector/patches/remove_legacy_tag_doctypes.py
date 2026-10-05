import frappe

LEGACY = ("Periskope Message Tag", "Periskope Message Tag Link")


def execute():
	"""Delete the legacy tag doctypes from the site (their data was moved to ai_category by an
	earlier patch). They no longer ship with the app, and a fresh install must not get them either
	— this covers sites that still have them sitting around empty."""
	for doctype in LEGACY:
		if not frappe.db.exists("DocType", doctype):
			continue
		if frappe.db.count(doctype) > 0:
			# Rows still here means move_to_ai_categories did not run or found nothing to move;
			# do not destroy data silently.
			continue
		frappe.delete_doc("DocType", doctype, ignore_missing=True, force=True)
