import frappe


def execute():
	"""Only text chats are analysed for now: mark saved images, documents, etc. as skipped."""
	frappe.db.sql(
		"""
		update `tabPeriskope Message`
		set classification_status = 'Skipped', classification_error = 'Not a text message'
		where ifnull(message_type, 'chat') != 'chat'
			and ifnull(classification_status, '') in ('', 'Pending', 'Failed')
		"""
	)
