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
		from periskope_connector.ai.classifier import PROVIDER_API, available_providers

		if (self.ai_provider or PROVIDER_API) not in available_providers():
			# Jarvis was chosen and has since been uninstalled.
			self.ai_provider = PROVIDER_API

		self.base_url = (self.base_url or "").strip().rstrip("/")
		from periskope_connector.ai.classifier import ai_address_problem

		self.ai_base_url = (self.ai_base_url or "").strip().rstrip("/")
		problem = ai_address_problem(self.ai_base_url, bool(self.get("ai_base_url_trusted")))
		if problem:
			frappe.throw(problem)
		self.validate_reminders()
		self.validate_assistant()

	def validate_assistant(self):
		from frappe.utils import validate_email_address

		if flt(self.reply_delay_minutes) < 0 or flt(self.reply_delay_minutes) > 120:
			frappe.throw(_("The reply time must be between 0 and 120 minutes"))
		emails = [e.strip() for e in re.split(r"[,\n;]", self.approver_emails or "") if e.strip()]
		for email in emails:
			if not validate_email_address(email):
				frappe.throw(_("{0} is not a valid email address").format(email))
		self.approver_emails = "\n".join(emails)
		if self.enable_auto_reply and self.has_value_changed("enable_auto_reply"):
			from periskope_connector.setup_status import missing_before_agent

			missing = missing_before_agent(self)
			if missing:
				frappe.throw(
					_("Finish the setup before the AI replies to customers:")
					+ "<br>"
					+ "<br>".join(frappe.utils.escape_html(m) for m in missing),
					title=_("Setup not finished"),
				)

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
		emails = [e.strip() for e in re.split(r"[,\n;]", self.alert_emails or "") if e.strip()]
		for email in emails:
			if not validate_email_address(email):
				frappe.throw(_("{0} is not a valid email address").format(email))
		self.alert_emails = ", ".join(emails)

		if self.enabled and not emails:
			frappe.msgprint(
				_("No Manager Emails are set, so nobody is emailed when the AI escalates."),
				indicator="orange",
			)
		self.webhook_url = webhook_url()
