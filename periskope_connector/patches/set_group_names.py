import frappe

from periskope_connector.client import fetch_chat_name


def execute():
	"""Give the group messages saved before Group Name existed their group's name."""
	frappe.reload_doc("periskope_connector", "doctype", "periskope_message")
	groups = frappe.db.sql(
		"""select chat_id, max(org_phone) from `tabPeriskope Message`
		where is_group = 1 and ifnull(chat_name, '') = '' group by chat_id"""
	)
	for chat_id, phone in groups:
		name = fetch_chat_name(chat_id, phone)
		if name:
			frappe.db.set_value(
				"Periskope Message", {"chat_id": chat_id}, "chat_name", name, update_modified=False
			)
