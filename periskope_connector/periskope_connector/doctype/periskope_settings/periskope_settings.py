import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt, get_url

WEBHOOK_METHOD = "periskope_connector.api.webhook.receive"


class PeriskopeSettings(Document):
	def onload(self):
		from periskope_connector.ai.classifier import available_providers

		# Jarvis is only offered when the Jarvis app is installed on this site.
		self.set_onload("ai_providers", available_providers())

	def validate(self):
		from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS, PROVIDER_API, available_providers

		if (self.ai_provider or PROVIDER_API) not in available_providers():
			# Jarvis was chosen and has since been uninstalled.
			self.ai_provider = PROVIDER_API

		self.base_url = (self.base_url or "").strip().rstrip("/")
		if not (self.ai_instructions or "").strip():
			self.ai_instructions = DEFAULT_INSTRUCTIONS
		self.validate_reminders()

	def validate_reminders(self):
		from frappe.utils import get_time, validate_email_address

		if self.working_hours_start and self.working_hours_end:
			if get_time(self.working_hours_start) >= get_time(self.working_hours_end):
				frappe.throw(_("Working Hours End must be after Working Hours Start"))
		for row in self.get("sla_levels") or []:
			if flt(row.wait_minutes) < 0:
				frappe.throw(_("Row {0}: Waiting minutes cannot be negative").format(row.idx))
			if row.action == "Email Recipients":
				listed = [e.strip() for e in re.split(r"[,\n;]", row.recipients or "") if e.strip()]
				if not listed or not all(validate_email_address(e) for e in listed):
					frappe.throw(_("Row {0}: Email Recipients needs valid email addresses").format(row.idx))
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
