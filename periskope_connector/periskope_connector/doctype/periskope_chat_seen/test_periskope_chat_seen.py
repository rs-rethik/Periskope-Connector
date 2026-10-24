import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = [*OTHER_APP_DOCTYPES, "Periskope Chat"]


class TestPeriskopeChatSeen(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_seen_test")

	def test_one_read_mark_per_chat_and_user(self):
		chat = frappe.get_doc({"doctype": "Periskope Chat", "chat_id": "919600077777@c.us"}).insert(
			ignore_permissions=True
		)
		row = {"doctype": "Periskope Chat Seen", "chat": chat.name, "user": "Administrator"}
		self.assertEqual(frappe.get_doc(row).insert().seen_key, f"{chat.name}|Administrator")
		self.assertRaises(frappe.UniqueValidationError, frappe.get_doc(row).insert)
