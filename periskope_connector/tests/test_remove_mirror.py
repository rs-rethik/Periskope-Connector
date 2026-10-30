"""The patch that removes the CRM WhatsApp tab copy (patches/remove_crm_whatsapp_mirror.py): the
Periskope account, its copied messages and their CRM notifications go; a real account stays."""

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector.patches import remove_crm_whatsapp_mirror as patch
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES
META = "_Test Meta Account"


def _account(name: str, default: int = 0):
	if frappe.db.exists("WhatsApp Account", name):
		return
	frappe.get_doc(
		{
			"doctype": "WhatsApp Account",
			"account_name": name,
			"status": "Active",
			"url": "https://example.invalid",
			"version": "v0",
			"phone_id": name,
			"token": "x",
			"is_default_incoming": default,
			"is_default_outgoing": default,
		}
	).insert(ignore_permissions=True)


def _message(account: str) -> str:
	doc = frappe.new_doc("WhatsApp Message")
	doc.update({"type": "Incoming", "from": "+919600000999", "message": "Hi", "whatsapp_account": account})
	doc.db_insert()  # as the copy was written: without the send code
	return doc.name


class TestRemoveMirror(FrappeTestCase):
	def setUp(self):
		if not frappe.db.exists("DocType", "WhatsApp Account"):
			self.skipTest("frappe_whatsapp is not installed")

	def test_the_periskope_account_and_its_copies_go_and_a_real_account_stays(self):
		frappe.db.set_value("WhatsApp Account", {"is_default_outgoing": 1}, "is_default_outgoing", 0)
		frappe.db.set_value("WhatsApp Account", {"is_default_incoming": 1}, "is_default_incoming", 0)
		_account(patch.ACCOUNT, default=1)
		_account(META)
		copy, real = _message(patch.ACCOUNT), _message(META)
		if frappe.db.exists("DocType", "CRM Notification"):
			frappe.db.delete("CRM Notification", {"notification_type_doc": copy})
			frappe.get_doc(
				{
					"doctype": "CRM Notification",
					"from_user": "Administrator",
					"to_user": "Administrator",
					"type": "WhatsApp",
					"notification_type_doctype": "WhatsApp Message",
					"notification_type_doc": copy,
				}
			).insert(ignore_permissions=True)
		frappe.db.set_single_value("WhatsApp Settings", "default_outgoing_account", patch.ACCOUNT)
		frappe.db.set_single_value("WhatsApp Settings", "default_incoming_account", META)

		patch.execute()

		self.assertFalse(frappe.db.exists("WhatsApp Account", patch.ACCOUNT))
		self.assertFalse(frappe.db.exists("WhatsApp Message", copy))
		self.assertTrue(frappe.db.exists("WhatsApp Message", real))
		self.assertTrue(frappe.db.exists("WhatsApp Account", META))
		self.assertFalse(frappe.db.get_single_value("WhatsApp Settings", "default_outgoing_account"))
		self.assertEqual(frappe.db.get_single_value("WhatsApp Settings", "default_incoming_account"), META)
		if frappe.db.exists("DocType", "CRM Notification"):
			self.assertFalse(frappe.db.exists("CRM Notification", {"notification_type_doc": copy}))
		patch.execute()  # a second run does nothing
