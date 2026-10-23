import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import access, numbers
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = [*OTHER_APP_DOCTYPES, "WhatsApp Number"]


class TestPeriskopeAgent(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_agent_test")

	def test_an_inactive_agent_has_no_numbers(self):
		email = "agent.test@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Agent", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", email).add_roles("Sales User")
		number = numbers.ensure("919600066666")
		agent = frappe.get_doc(
			{"doctype": "Periskope Agent", "user": email, "numbers": [{"whatsapp_number": number}]}
		).insert(ignore_permissions=True)
		self.assertEqual(access.allowed_numbers(email), [number])
		agent.db_set("is_active", 0)
		self.assertEqual(access.allowed_numbers(email), [])
