import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_url

WEBHOOK_METHOD = "periskope_connector.api.webhook.receive"


class PeriskopeSettings(Document):
	def validate(self):
		from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS

		self.base_url = (self.base_url or "").strip().rstrip("/")
		if not (self.ai_instructions or "").strip():
			self.ai_instructions = DEFAULT_INSTRUCTIONS
		self.validate_reminders()

	def validate_reminders(self):
		from frappe.utils import get_time, validate_email_address

		if self.working_hours_start and self.working_hours_end:
			if get_time(self.working_hours_start) >= get_time(self.working_hours_end):
				frappe.throw(_("Working Hours End must be after Working Hours Start"))
		for field, default in (("remind_after_hours", 1), ("escalate_after_hours", 2)):
			if not flt(self.get(field)):
				self.set(field, default)  # never saved yet, or cleared
			elif flt(self.get(field)) < 0:
				frappe.throw(_("{0} cannot be negative").format(self.meta.get_label(field)))

		emails = [e.strip() for e in re.split(r"[,\n;]", self.alert_emails or "") if e.strip()]
		for email in emails:
			if not validate_email_address(email):
				frappe.throw(_("{0} is not a valid email address").format(email))
		self.alert_emails = ", ".join(emails)

		if self.enable_reply_reminders and not emails:
			frappe.msgprint(
				_(
					"No Manager Emails are set, so reminders will create ToDos but managers will not be emailed."
				),
				indicator="orange",
			)
		self.webhook_url = f"{get_url()}/api/method/{WEBHOOK_METHOD}"
