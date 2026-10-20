"""The AI agent now decides what happens to a message, so the rule doctypes are gone from the app
(keyword rules and their alerts, tag actions, SLA levels), and so are the lead and deal page scripts
that paused the AI. Remove what earlier versions left in the database."""

import frappe

DOCTYPES = (
	"Periskope Keyword Alert",
	"Periskope Keyword Rule",
	"Periskope Tag Action",
	"Periskope SLA Level",
)
FORM_SCRIPTS = ("Periskope AI (CRM Deal)", "Periskope AI (CRM Lead)")


def execute():
	for doctype in DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
	if frappe.db.exists("DocType", "CRM Form Script"):
		for name in FORM_SCRIPTS:
			if frappe.db.exists("CRM Form Script", name):
				frappe.delete_doc("CRM Form Script", name, force=True, ignore_permissions=True)
