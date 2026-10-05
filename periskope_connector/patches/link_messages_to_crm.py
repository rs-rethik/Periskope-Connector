import frappe

from periskope_connector.linking import ensure_group, link_unlinked


def execute():
	"""Create the groups of the messages saved before Periskope Group existed, then link the messages."""
	frappe.reload_doc("periskope_connector", "doctype", "periskope_group")
	frappe.reload_doc("periskope_connector", "doctype", "periskope_message")
	groups = frappe.db.sql(
		"select chat_id, max(org_phone) from `tabPeriskope Message` where is_group = 1 group by chat_id"
	)
	for chat_id, phone in groups:
		ensure_group(chat_id, phone)
	link_unlinked()
