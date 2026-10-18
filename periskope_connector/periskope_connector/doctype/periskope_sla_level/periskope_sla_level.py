import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, validate_email_address


class PeriskopeSLALevel(Document):
	"""One step of the reply-reminder ladder (reminders.py reads them in order of waiting time)."""

	def validate(self):
		if flt(self.wait_minutes) < 0:
			frappe.throw(_("Waiting minutes cannot be negative"))
		if self.action == "Email Recipients":
			listed = [e.strip() for e in re.split(r"[,\n;]", self.recipients or "") if e.strip()]
			if not listed or not all(validate_email_address(e) for e in listed):
				frappe.throw(_("Email Recipients needs valid email addresses"))
			self.recipients = ", ".join(listed)
