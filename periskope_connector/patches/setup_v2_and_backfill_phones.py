import frappe


def execute():
	"""v2 upgrade: create the normalised phone custom fields (if the site does not have them yet)
	and back-fill them from existing lead/deal numbers, so indexed matching works from minute one."""
	from periskope_connector.install import setup
	from periskope_connector.linking import backfill_phone_fields

	setup()
	if frappe.db.exists("DocType", "CRM Lead"):
		backfill_phone_fields("CRM Lead")
	if frappe.db.exists("DocType", "CRM Deal"):
		backfill_phone_fields("CRM Deal")
