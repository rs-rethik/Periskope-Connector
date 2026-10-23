import frappe
from frappe.model.document import Document


class PeriskopeMessage(Document):
	pass


def on_doctype_update():
	# message_timestamp has its own index (search_index in the doctype), which serves the
	# reminder check, the list view and the workspace card and chart. This composite index
	# serves the AI's chat-history lookup (one chat, newest first): with only the timestamp
	# index that query still reads thousands of rows, and this one reads about ten.
	frappe.db.add_index("Periskope Message", ["chat_id", "message_timestamp"])
	# The inbox loads one chat row's history (one number), newest first, a page at a time.
	frappe.db.add_index("Periskope Message", ["periskope_chat", "message_timestamp"])
