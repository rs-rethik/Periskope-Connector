from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import alerts, messages
from periskope_connector.tests.test_linking import _event

ROWS = [{"recipients": ""}]  # an Email Alert action with no recipients: the Manager Emails


class TestHighSeverityAlert(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: ""),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_alert_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_alert_test")
		self.email = frappe.db.get_value("User", "Administrator", "email")
		cfg = patch("periskope_connector.alerts.get_config")
		self.config = cfg.start()
		self.config.return_value.emails = [self.email, "md@example.com"]
		self.addCleanup(cfg.stop)
		mail = patch.object(frappe, "sendmail")
		self.sendmail = mail.start()
		self.addCleanup(mail.stop)

	def _message(self, severity="High", body="This is unacceptable <b>now</b>"):
		name = messages.create_message(
			_event(message_id=f"al_{severity}_{frappe.generate_hash(length=4)}", body=body)
		)
		frappe.db.set_value(
			"Periskope Message",
			name,
			{
				"severity": severity,
				"ai_category": "quality_complaint",
				"ai_summary": "Angry about torn fabric",
			},
		)
		return name

	def test_a_high_severity_message_alerts_the_managers_once(self):
		name = self._message()
		self.assertTrue(alerts.send_alert(name, ROWS))
		kwargs = self.sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], [self.email, "md@example.com"])
		self.assertIn("quality complaint", kwargs["subject"])
		self.assertIn("Angry about torn fabric", kwargs["message"])
		self.assertIn("&lt;b&gt;now&lt;/b&gt;", kwargs["message"])  # customer text is escaped
		self.assertTrue(
			frappe.db.exists("Notification Log", {"for_user": "Administrator", "document_name": name})
		)
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "high_alert_sent"), 1)

		self.assertFalse(alerts.send_alert(name, ROWS))  # not a second time
		self.assertEqual(self.sendmail.call_count, 1)

	def test_missing_recipients_send_nothing(self):
		self.config.return_value.emails = []
		self.assertFalse(alerts.send_alert(self._message("High"), ROWS))
		self.sendmail.assert_not_called()

	def test_recipients_on_the_action_replace_the_manager_emails(self):
		name = self._message()
		self.assertTrue(alerts.send_alert(name, [{"recipients": "qa@example.com; head@example.com"}]))
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], ["qa@example.com", "head@example.com"])

	def test_several_actions_send_one_alert_to_everyone(self):
		name = self._message()
		rows = [{"recipients": "qa@example.com"}, {"recipients": ""}]
		self.assertTrue(alerts.send_alert(name, rows))
		self.assertEqual(self.sendmail.call_count, 1)
		self.assertEqual(
			self.sendmail.call_args.kwargs["recipients"], ["qa@example.com", self.email, "md@example.com"]
		)

	def test_a_failure_is_logged_and_never_raised(self):
		self.sendmail.side_effect = RuntimeError("mail down")
		with patch.object(frappe, "log_error") as log_error:
			self.assertFalse(alerts.send_alert(self._message(), ROWS))
		log_error.assert_called_once()
