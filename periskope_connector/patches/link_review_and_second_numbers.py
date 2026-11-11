"""Two linking changes reach the data already there:
- leads and deals with both a Mobile and a different Phone number get the second one matched too
  (periskope_phone2_e164);
- chats whose newest message was matched to more than one open deal or lead are flagged for a check
  (Periskope Chat.link_review), unless a person linked them by hand."""

import frappe

from periskope_connector.linking import phone_values


def execute():
	from periskope_connector.install import _ensure_custom_fields

	_ensure_custom_fields()  # the new field: patches run before after_migrate makes it
	for doctype in ("CRM Lead", "CRM Deal"):
		if not frappe.db.has_column(doctype, "periskope_phone2_e164"):
			continue
		for row in frappe.get_all(
			doctype,
			filters=[["mobile_no", "is", "set"], ["phone", "is", "set"]],
			fields=["name", "mobile_no", "phone"],
		):
			second = phone_values(row.mobile_no, row.phone)["periskope_phone2_e164"]
			if second:
				frappe.db.set_value(doctype, row.name, "periskope_phone2_e164", second, update_modified=False)

	chats = frappe.get_all(
		"Periskope Message",
		filters={"link_status": "Ambiguous", "periskope_chat": ["is", "set"]},
		pluck="periskope_chat",
		distinct=True,
	)
	for chat in chats:
		newest = frappe.get_all(
			"Periskope Message",
			filters={"periskope_chat": chat},
			pluck="link_status",
			order_by="message_timestamp desc",
			limit=1,
		)
		if (
			newest
			and newest[0] == "Ambiguous"
			and not frappe.db.get_value("Periskope Chat", chat, "linked_by_hand")
		):
			frappe.db.set_value("Periskope Chat", chat, "link_review", 1, update_modified=False)
