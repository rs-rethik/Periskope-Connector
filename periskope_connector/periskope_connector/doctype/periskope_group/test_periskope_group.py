"""Tests for the Periskope Group doctype and the code that works on it."""

from frappe.tests.utils import FrappeTestCase

from periskope_connector import linking
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


class TestPeriskopeGroup(FrappeTestCase):
	pass


class TestNameMatching(FrappeTestCase):
	def test_number_key_uses_the_last_ten_digits(self):
		self.assertEqual(linking.number_key("+91 98765-43210"), "9876543210")
		self.assertEqual(linking.number_key("919876543210@c.us"), "9876543210")
		self.assertEqual(linking.number_key("12345"), "")

	def test_names_match_ignores_case_punctuation_and_company_words(self):
		self.assertTrue(linking.names_match("Sumi Export House Pvt. Ltd.", "SUMI EXPORT HOUSE"))
		self.assertTrue(linking.names_match("Sumi Export House - Tirupur", "Sumi Export House"))

	def test_names_do_not_match_when_short_or_different(self):
		self.assertFalse(linking.names_match("Sun", "Sun Tex Mills"))
		self.assertFalse(linking.names_match("Grace Impex", "Coral Reef Cottons"))
		self.assertFalse(linking.names_match("", "Sumi Export House"))
