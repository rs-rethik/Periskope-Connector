import frappe


# Frappe runs execute() by name from patches.txt on `bench migrate` (dynamic dispatch),
# so nothing in this app calls it directly. Do not remove it: it would break migrations.
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
