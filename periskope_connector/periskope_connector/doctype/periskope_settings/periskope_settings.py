import re

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt

from periskope_connector.webhooks import webhook_url

MIN_WORKING_MINUTES = 30


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
		self.validate_assistant()

	def validate_assistant(self):
		from frappe.utils import validate_email_address

		if flt(self.reply_delay_minutes) < 0 or flt(self.reply_delay_minutes) > 120:
			frappe.throw(_("Reply Delay must be between 0 and 120 minutes"))
		if not 0 <= (self.reply_delay_jitter_percent or 0) <= 100:
			frappe.throw(_("Random Variation must be between 0 and 100 percent"))
		if not 0 <= flt(self.reply_min_confidence) <= 100:
			frappe.throw(_("Minimum Confidence must be between 0 and 100"))
		emails = [e.strip() for e in re.split(r"[,\n;]", self.suggestion_recipients or "") if e.strip()]
		for email in emails:
			if not validate_email_address(email):
				frappe.throw(_("{0} is not a valid email address").format(email))
		self.suggestion_recipients = ", ".join(emails)
		if self.enable_auto_reply and not (self.holding_message or "").strip():
			frappe.throw(_("Set a Holding Message before switching Auto Reply on"))

	def validate_reminders(self):
		from frappe.utils import get_time, validate_email_address

		if self.working_hours_start and self.working_hours_end:
			start, end = get_time(self.working_hours_start), get_time(self.working_hours_end)
			if (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute) < MIN_WORKING_MINUTES:
				frappe.throw(
					_("Working Hours End must be at least {0} minutes after Working Hours Start").format(
						MIN_WORKING_MINUTES
					)
				)
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
		self.webhook_url = webhook_url()
