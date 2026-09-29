from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import install


class TestAfterInstall(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_install_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_install_test")

	def test_creates_the_default_tags(self):
		with patch.object(install, "create_default_tags") as create:
			install.after_install()
		create.assert_called_once_with()

	def test_a_failure_is_logged_reported_and_does_not_abort_the_install(self):
		with (
			patch.object(install, "create_default_tags", side_effect=RuntimeError("database error")),
			patch.object(frappe, "log_error") as log_error,
			patch.object(install.click, "secho") as secho,
		):
			install.after_install()  # must not raise

		log_error.assert_called_once()
		self.assertIn("default message tags", log_error.call_args.kwargs["title"])
		# the user is told what to run to fix it
		self.assertIn("default_tags.create_default_tags", secho.call_args.args[0])

	def test_a_failure_undoes_only_the_seeding(self):
		marker = frappe.get_doc({"doctype": "ToDo", "description": "install step that succeeded"}).insert(
			ignore_permissions=True
		)

		def half_done():
			frappe.get_doc({"doctype": "ToDo", "description": "half of the seeding"}).insert(
				ignore_permissions=True
			)
			raise RuntimeError("fails after writing")

		with (
			patch.object(install, "create_default_tags", half_done),
			patch.object(frappe, "log_error"),
			patch.object(install.click, "secho"),
		):
			install.after_install()

		self.assertTrue(frappe.db.exists("ToDo", marker.name))  # earlier install work is kept
		self.assertFalse(frappe.db.exists("ToDo", {"description": "half of the seeding"}))
