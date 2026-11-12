"""Tests for the Periskope Erasure Log doctype: the audit row of an erase (api/privacy.py) keeps no
number, and nobody makes or changes one by hand."""

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES


class TestPeriskopeErasureLog(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_erasure_log_test")

	def test_it_is_read_only_for_the_roles_that_see_it(self):
		meta = frappe.get_meta("Periskope Erasure Log")
		self.assertTrue(meta.in_create)
		for perm in meta.permissions:
			self.assertFalse(perm.create or perm.write or perm.delete, perm.role)
		self.assertTrue(all(f.read_only for f in meta.fields))
