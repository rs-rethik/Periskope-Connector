import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


class TestWhatsAppNumber(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_number_test")

	def test_the_number_is_kept_as_digits_and_names_the_row(self):
		doc = frappe.get_doc({"doctype": "WhatsApp Number", "number": "+91 97863-87000@c.us"}).insert()
		self.assertEqual((doc.name, doc.number), ("919786387000", "919786387000"))
