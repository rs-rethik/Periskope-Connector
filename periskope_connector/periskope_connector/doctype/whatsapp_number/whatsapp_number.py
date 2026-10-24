import re

from frappe.model.document import Document


class WhatsAppNumber(Document):
	def before_insert(self):
		self.number = digits(self.number)  # named by the number, so cleaned before naming

	def validate(self):
		self.number = digits(self.number)


def digits(value: str | None) -> str:
	"""A WhatsApp number as Periskope gives it: digits only, without "@c.us"."""
	return re.sub(r"\D", "", (value or "").split("@")[0])
