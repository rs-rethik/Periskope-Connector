from frappe.model.document import Document


class PeriskopeChatSeen(Document):
	def before_insert(self):
		self.seen_key = f"{self.chat}|{self.user}"
