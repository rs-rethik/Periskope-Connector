"""Every Periskope screen is now in the WhatsApp inbox (/whatsapp), so the Desk versions are gone from
the app: the Periskope workspace with its number cards, charts and Messages by Tag block, and the
Knowledge Base desk page. Remove what earlier versions left in the database. The doctypes stay in Desk
for System Managers."""

import frappe

MODULE = "Periskope Connector"
WORKSPACE = "Periskope"
BLOCK = "Periskope Messages by Tag"
PAGE = "periskope-knowledge"


def execute():
	# the workspace links to the cards, charts and block, so it goes first. The app ships no cards or
	# charts any more, so every one of its module goes, including those earlier versions renamed.
	for doctype, names in (
		("Workspace", [WORKSPACE, *frappe.get_all("Workspace", {"module": MODULE}, pluck="name")]),
		("Custom HTML Block", [BLOCK]),
		("Number Card", frappe.get_all("Number Card", {"module": MODULE}, pluck="name")),
		("Dashboard Chart", frappe.get_all("Dashboard Chart", {"module": MODULE}, pluck="name")),
		("Page", [PAGE]),
	):
		for name in dict.fromkeys(names):
			if frappe.db.exists(doctype, name):
				frappe.delete_doc(doctype, name, force=True, ignore_permissions=True)
