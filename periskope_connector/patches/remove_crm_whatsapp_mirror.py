"""The Periskope chats are no longer copied into the CRM's WhatsApp tab: the WhatsApp app (/whatsapp)
shows them with the lead or deal beside each chat. Remove what the copy left behind:

- the "Periskope" WhatsApp Account (no Meta credentials) and the WhatsApp Message rows copied under it,
  with the CRM notifications that pointed at those rows,
- the account in WhatsApp Settings, where it was the default (a real Meta account is left as it is)."""

import frappe

ACCOUNT = "Periskope"


def execute():
	if not frappe.db.exists("DocType", "WhatsApp Account") or not frappe.db.exists(
		"WhatsApp Account", ACCOUNT
	):
		return
	if frappe.db.exists("DocType", "WhatsApp Message"):
		copies = frappe.get_all("WhatsApp Message", filters={"whatsapp_account": ACCOUNT}, pluck="name")
		if copies and frappe.db.exists("DocType", "CRM Notification"):
			frappe.db.delete(
				"CRM Notification",
				{"notification_type_doctype": "WhatsApp Message", "notification_type_doc": ["in", copies]},
			)
		frappe.db.delete("WhatsApp Message", {"whatsapp_account": ACCOUNT})
	if frappe.db.exists("DocType", "WhatsApp Settings"):
		for field in ("default_outgoing_account", "default_incoming_account"):
			if frappe.db.get_single_value("WhatsApp Settings", field) == ACCOUNT:
				frappe.db.set_single_value("WhatsApp Settings", field, None)
	frappe.delete_doc("WhatsApp Account", ACCOUNT, ignore_permissions=True, force=True)
