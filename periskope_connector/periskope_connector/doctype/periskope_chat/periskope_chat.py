import frappe
from frappe.model.document import Document


class PeriskopeChat(Document):
	def before_insert(self):
		self.chat_key = key(self.whatsapp_number or self.org_phone, self.chat_id)


def key(number: str | None, chat_id: str) -> str:
	"""One chat row per (our number, the contact's chat id): the same contact on two of our
	numbers is two chats."""
	return f"{number or ''}|{chat_id}"


def on_doctype_update():
	# The inbox list: one number's (or all permitted numbers') chats, newest first.
	frappe.db.add_index("Periskope Chat", ["whatsapp_number", "last_message_at"])
