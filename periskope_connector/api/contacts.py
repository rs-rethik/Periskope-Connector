"""Whitelisted actions on the contact queue (the Periskope Contact form and list).

All of them are for managers: a rep has no access to the queue in the pilot.
"""

import frappe

MANAGERS = ("System Manager", "Sales Manager", "WhatsApp Manager")


@frappe.whitelist()
def create_lead(contact: str) -> str:
	frappe.only_for(MANAGERS)
	from periskope_connector.contacts import create_lead_for_contact

	name = create_lead_for_contact(contact)
	frappe.clear_messages()
	return name


@frappe.whitelist()
def link_to(contact: str, lead: str | None = None, deal: str | None = None) -> None:
	frappe.only_for(MANAGERS)
	from periskope_connector.contacts import link_contact

	link_contact(contact, lead=lead, deal=deal)


@frappe.whitelist()
def ignore(contact: str, reason: str) -> None:
	frappe.only_for(MANAGERS)
	from periskope_connector.contacts import ignore_contact

	ignore_contact(contact, reason)


@frappe.whitelist()
def find_matches(contact: str) -> list[dict]:
	"""Search leads and deals by this number and name, for the 'link to existing' dialog."""
	frappe.only_for(MANAGERS)
	doc = frappe.get_doc("Periskope Contact", contact)
	number = doc.phone or ""
	key = number[-10:] if len(number) >= 10 else number
	name = (doc.display_name or "").strip()

	matches = []
	for doctype, label, title_field in (
		("CRM Lead", "Lead", "lead_name"),
		("CRM Deal", "Deal", "organization_name"),
	):
		or_filters = [
			["periskope_phone_e164", "=", number],
			["periskope_phone_key", "=", key],
		]
		if name and len(name) >= 3:
			or_filters.append([title_field, "like", f"%{name}%"])
		rows = frappe.get_all(doctype, or_filters=or_filters, fields=["name", title_field], limit=20)
		for row in rows:
			matches.append(
				{
					"doctype": doctype,
					"label": label,
					"name": row.name,
					"title": row.get(title_field) or row.name,
				}
			)
	return matches
