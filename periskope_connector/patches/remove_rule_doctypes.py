"""The AI agent now decides what happens to a message, so the rule doctypes are gone from the app
(keyword rules and their alerts, tag actions, SLA levels), and so are the lead and deal page scripts
that paused the AI and the workspace cards the clean-up replaced. Remove what earlier versions left in
the database."""

import frappe

DOCTYPES = (
	"Periskope Keyword Alert",
	"Periskope Keyword Rule",
	"Periskope Tag Action",
	"Periskope SLA Level",
)
FORM_SCRIPTS = ("Periskope AI (CRM Deal)", "Periskope AI (CRM Lead)")
NUMBER_CARDS = (
	"Waiting for Rep Reply",
	"Open Escalations",
	"High Severity Issues",
	"AI Tagging Failed",
	"Holding Messages Today",
)
CHARTS = ("Messages by Sentiment",)


def execute():
	for doctype in DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_permissions=True)
	if frappe.db.exists("DocType", "CRM Form Script"):
		for name in FORM_SCRIPTS:
			if frappe.db.exists("CRM Form Script", name):
				frappe.delete_doc("CRM Form Script", name, force=True, ignore_permissions=True)
	for doctype, names in (("Number Card", NUMBER_CARDS), ("Dashboard Chart", CHARTS)):
		for name in names:
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
