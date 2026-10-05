"""Gate 1 matching tests: normalisation, E.164 safety, open-deal preference, phone-field upkeep."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import linking, messages
from periskope_connector.tests.test_linking import _event


class TestNormalisation(FrappeTestCase):
	def test_number_key_uses_the_last_ten_digits(self):
		self.assertEqual(linking.number_key("+91 98765-43210"), "9876543210")
		self.assertEqual(linking.number_key("919876543210@c.us"), "9876543210")
		self.assertEqual(linking.number_key("12345"), "")

	def test_e164_keeps_the_country_code(self):
		self.assertEqual(linking.phone_e164("+94 77 123 4567"), "94771234567")
		self.assertEqual(linking.phone_e164("0-98765 43210"), "09876543210")  # not stripped to 10
		self.assertEqual(linking.phone_e164(" +919600000001@c.us "), "919600000001")
		self.assertEqual(linking.phone_e164(None), "")


class TestPhoneFields(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_matching_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_matching_test")

	def _lead(self, **values):
		doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Test", **values})
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		return doc

	def _deal(self, **values):
		doc = frappe.get_doc({"doctype": "CRM Deal", **values})
		doc.flags.ignore_mandatory = True
		doc.flags.ignore_links = True
		doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		return doc

	def test_saving_a_lead_fills_the_normalised_fields(self):
		lead = self._lead(mobile_no="+91 96000 00001")
		values = frappe.db.get_value(
			"CRM Lead", lead.name, ["periskope_phone_e164", "periskope_phone_key"], as_dict=True
		)
		self.assertEqual(
			(values.periskope_phone_e164, values.periskope_phone_key), ("919600000001", "9600000001")
		)

	def test_editing_the_number_updates_them(self):
		lead = self._lead(mobile_no="9600000001")
		frappe.db.set_value("CRM Lead", lead.name, "mobile_no", "+91 98400 00002")
		linking.update_phone_fields(frappe.get_doc("CRM Lead", lead.name))
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "periskope_phone_key"), "9840000002")

	def test_by_mobile_finds_through_the_indexed_fields(self):
		lead = self._lead(mobile_no="+91 96000 00001")
		self.assertEqual(linking.by_mobile("919600000001@c.us"), [(lead.name, None)])

	def test_a_foreign_number_never_matches_through_the_last_ten_key(self):
		"""The whole point of E.164: two customers whose numbers share the last ten digits are two
		customers, and only the exact full number links them."""
		indian = self._lead(mobile_no="+919600000001")
		bangladeshi = self._lead(mobile_no="+8809600000001")  # same last ten digits

		self.assertEqual(linking.by_mobile("919600000001"), [(indian.name, None)])
		self.assertEqual(linking.by_mobile("8809600000001"), [(bangladeshi.name, None)])

	def test_an_open_deal_beats_a_newer_closed_deal(self):
		open_lead = self._lead()
		open_deal = self._deal(lead=open_lead.name, mobile_no="9600000001")  # default status type: Open
		closed_lead = self._lead()
		closed_deal = self._deal(lead=closed_lead.name, mobile_no="9600000001", status="Lost")
		# even newly edited, the closed deal must not shadow the open one
		frappe.db.set_value("CRM Deal", closed_deal.name, "modified", "2035-01-01 00:00:00")

		lead, deal, status = linking.resolve(linking.by_mobile("9600000001"))
		self.assertEqual((lead, deal, status), (open_lead.name, open_deal.name, "Linked"))

	def test_two_open_deals_are_ambiguous(self):
		first_lead = self._lead()
		self._deal(lead=first_lead.name, mobile_no="9600000001")
		second_lead = self._lead()
		second = self._deal(lead=second_lead.name, mobile_no="9600000001")
		frappe.db.set_value("CRM Deal", second.name, "modified", "2035-01-01 00:00:00")

		lead, deal, status = linking.resolve(linking.by_mobile("9600000001"))
		self.assertEqual((lead, deal, status), (second_lead.name, second.name, "Ambiguous"))

	def test_with_only_closed_deals_the_newest_one_wins(self):
		first_lead = self._lead()
		self._deal(lead=first_lead.name, mobile_no="9600000001", status="Lost")
		second_lead = self._lead()
		won = self._deal(lead=second_lead.name, mobile_no="9600000001", status="Won")

		lead, deal, status = linking.resolve(linking.by_mobile("9600000001"))
		self.assertEqual((lead, deal, status), (second_lead.name, won.name, "Linked"))

	def test_backfill_fills_only_rows_that_miss_the_fields(self):
		lead = self._lead(mobile_no="9600000001")
		frappe.db.set_value("CRM Lead", lead.name, {"periskope_phone_e164": "", "periskope_phone_key": ""})
		self.assertEqual(linking.backfill_phone_fields("CRM Lead"), 1)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead.name, "periskope_phone_key"), "9600000001")


class TestIngestUsesTheIndexedMatching(FrappeTestCase):
	"""The full Gate 1 chain: a message from a number in the CRM lands on the right lead."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch.object(messages, "_rep_for_phone", lambda n: None),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_ingest_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_ingest_test")

	def test_a_message_from_a_known_number_links_on_the_indexed_fields(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "+91 96000 00001"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		with patch.object(linking, "by_mobile", wraps=linking.by_mobile) as by_mobile:
			name = messages.create_message(_event(message_id="idx1"))
			by_mobile.assert_called_once()
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "crm_lead"), lead.name)
