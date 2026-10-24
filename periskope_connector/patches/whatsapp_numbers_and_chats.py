"""Move to one WhatsApp Number row per company number and one chat row per (number, contact).

Earlier versions kept the numbers in a table inside Periskope Settings (Periskope Phone) and one
Periskope Chat per contact, named by the contact's chat id, whatever number they wrote to. This
patch:
1. copies the numbers (with their rep and sales person) into WhatsApp Number, and adds any number
   seen on a message,
2. gives every message its number and splits each contact's chat per number (a new row copies the
   contact's lead, deal, organization and summary note),
3. points every message at its chat row and fills the inbox fields of each chat.
It can run again: everything it writes is looked up first.
"""

import frappe

from periskope_connector import chat, numbers
from periskope_connector.periskope_connector.doctype.periskope_chat.periskope_chat import key
from periskope_connector.system import insert_as_system

COPIED = ["chat_name", "is_group", "contact_number", "crm_lead", "crm_deal", "organization", "summary_note"]


def _copy_old_phones():
	if not frappe.db.table_exists("Periskope Phone"):
		return
	rows = frappe.db.sql(
		"""select phone_id, phone_name, number, wa_state, is_ready, rep, sales_person
		from `tabPeriskope Phone` where parent = 'Periskope Settings'""",
		as_dict=True,
	)
	for row in rows:
		name = numbers.ensure(row.number)
		if not name:
			continue
		frappe.db.set_value(
			"WhatsApp Number",
			name,
			{
				"periskope_phone_id": row.phone_id,
				"phone_name": row.phone_name,
				"display_name": row.phone_name or name,
				"wa_state": row.wa_state,
				"is_ready": row.is_ready,
				"default_assignee": row.rep,
				"sales_person": row.sales_person,
			},
		)
	if frappe.db.exists("DocType", "Periskope Phone"):
		frappe.delete_doc("DocType", "Periskope Phone", force=True, ignore_permissions=True)


def _numbers_on_messages():
	for number in frappe.db.sql_list(
		"select distinct org_phone from `tabPeriskope Message` where ifnull(org_phone, '') != ''"
	):
		numbers.ensure(number)
	frappe.db.sql(
		"""update `tabPeriskope Message` m join `tabWhatsApp Number` n on n.name = m.org_phone
		set m.whatsapp_number = n.name where ifnull(m.whatsapp_number, '') = ''"""
	)


def _split_chats():
	# Existing rows keep their name and become the chat on their own number.
	for row in frappe.get_all("Periskope Chat", fields=["name", "chat_id", "org_phone", "chat_key"]):
		if row.chat_key:
			continue
		number = row.org_phone if frappe.db.exists("WhatsApp Number", row.org_phone or "") else None
		frappe.db.set_value(
			"Periskope Chat",
			row.name,
			{"chat_key": key(number or row.org_phone, row.chat_id), "whatsapp_number": number},
			update_modified=False,
		)
	# A contact who wrote to another of our numbers gets a chat row there too.
	pairs = frappe.db.sql(
		"""select distinct chat_id, ifnull(org_phone, '') as org_phone from `tabPeriskope Message`
		where ifnull(chat_id, '') != ''""",
		as_dict=True,
	)
	for pair in pairs:
		if chat.name_of(pair.org_phone, pair.chat_id):
			continue
		source = frappe.db.get_value("Periskope Chat", {"chat_id": pair.chat_id}, COPIED, as_dict=True) or {}
		doc = frappe.get_doc(
			{
				"doctype": "Periskope Chat",
				"chat_id": pair.chat_id,
				"org_phone": pair.org_phone,
				"whatsapp_number": pair.org_phone or None,
				"status": "Open",
				**{field: source.get(field) for field in COPIED if source.get(field) is not None},
			}
		)
		# A copy of what the contact's chat already holds: a lead or note deleted since must not stop
		# the migration.
		doc.flags.ignore_links = True
		insert_as_system(doc)


def _link_messages():
	frappe.db.sql(
		"""update `tabPeriskope Message` m join `tabPeriskope Chat` c
		on c.chat_key = concat(ifnull(m.org_phone, ''), '|', m.chat_id)
		set m.periskope_chat = c.name where ifnull(m.periskope_chat, '') = ''"""
	)
	for name in frappe.get_all("Periskope Chat", pluck="name"):
		chat.refresh(name)


def execute():
	frappe.reload_doc("periskope_connector", "doctype", "whatsapp_number")
	frappe.reload_doc("periskope_connector", "doctype", "periskope_chat")
	frappe.reload_doc("periskope_connector", "doctype", "periskope_message")
	_copy_old_phones()
	_numbers_on_messages()
	_split_chats()
	_link_messages()
