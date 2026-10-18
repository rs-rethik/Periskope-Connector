import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import validate_email_address


class PeriskopeKeywordRule(Document):
	"""Words that alert management (keywords.py)."""

	def validate(self):
		listed = [e.strip() for e in re.split(r"[,\n;]", self.recipients or "") if e.strip()]
		for email in listed:
			if not validate_email_address(email):
				frappe.throw(_("{0} is not a valid email address").format(email))
		self.recipients = ", ".join(listed)
