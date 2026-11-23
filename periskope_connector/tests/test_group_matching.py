"""Tests for how a WhatsApp group finds its customer: plural-tolerant names, a member who is a known
customer contact, the AI's group suggestions, and the AI budget a backlog may use."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import health, learner, linking, messages
from periskope_connector.ai import classifier
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

GROUP = "120363888000222@g.us"
CUSTOMER = "8155009911"  # a buyer's contact, on the customer's deals
COLLEAGUE = "9943009911"  # one of our users, also saved on another customer's contact


def _org(name):
	if not frappe.db.exists("CRM Organization", name):
		frappe.get_doc({"doctype": "CRM Organization", "organization_name": name}).insert(
			ignore_permissions=True
		)
	return name


def _deal(organization, contact=None):
	doc = frappe.get_doc(
		{
			"doctype": "CRM Deal",
			"organization": _org(organization),
			"deal_owner": "Administrator",
			"contacts": [{"contact": contact}] if contact else [],
		}
	)
	doc.flags.ignore_mandatory = doc.flags.ignore_links = doc.flags.ignore_validate = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _contact(name, phone):
	doc = frappe.get_doc({"doctype": "Contact", "first_name": name, "phone_nos": [{"phone": phone}]})
	doc.insert(ignore_permissions=True)
	return doc.name


class GroupCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_group_matching_test")
		settings(enabled=1, test_mode=0)
		for p in (
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Mgx Apparel - Pranera"),
			patch.object(messages, "fetch_chat_name", lambda *a, **k: "Mgx Apparel - Pranera"),
			patch("periskope_connector.ai.classifier.queue", lambda *a, **k: None),
		):
			p.start()
			self.addCleanup(p.stop)
		frappe.db.delete("Periskope Group", {"chat_id": GROUP})
		self.addCleanup(linking.staff_numbers.clear_cache)
		self.addCleanup(frappe.cache().delete_value, linking.ORGANIZATIONS_KEY)
		linking.staff_numbers.clear_cache()
		frappe.cache().delete_value(linking.ORGANIZATIONS_KEY)
		email = "group.match.colleague@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Colleague", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.db.set_value("User", email, {"mobile_no": COLLEAGUE, "enabled": 1, "user_type": "System User"})
		self.n = 0

	def message(self, sender):
		self.n += 1
		return messages.create_message(
			event(message_id=f"group_match_{self.n}", chat_id=GROUP, sender_phone=f"91{sender}@c.us")
		)

	def group(self):
		return frappe.db.get_value("Periskope Group", GROUP, ["group_type", "organization"], as_dict=True)


class TestNames(FrappeTestCase):
	def test_a_plural_or_singular_name_is_the_same_company(self):
		# The test site's 'Mg Apparel - Pranera' group was taken for a team group for this.
		self.assertTrue(linking.names_match("Mg Apparel - Pranera", "Mg apparels"))
		self.assertTrue(linking.names_match("ARROW EXPORTS PVT LTD", "Arrow Export"))
		self.assertEqual(linking.name_key("Glass Works"), "glass work")  # 'ss' is not a plural
		self.assertFalse(linking.names_match("Mg Apparel", "Mgv Garments"))
		# one generic word inside a longer name is not that company
		self.assertFalse(linking.names_match("Textiles - Garment Sales", "GARMENTS"))
		self.assertTrue(linking.names_match("Innovative - Pranera", "Innovative"))

	def test_a_group_name_resembles_a_company_by_a_telling_word(self):
		self.assertTrue(linking.resembles("Mg apparels", "Mg Apparel - Pranera"))
		self.assertTrue(linking.resembles("TEJWANEY BROTHERS", "Pranera - Tejwaney Bros"))
		self.assertTrue(linking.resembles("COROQLO CLOTHING PVT LTD", "Corpqlo ~ Pranera"))  # misspelt
		self.assertFalse(linking.resembles("AERELE TECHNOLOGIES", "CRM Support"))
		self.assertFalse(linking.resembles("Sri Garments", "Garments Team"))  # trade words only


class TestMembers(GroupCase):
	def test_a_customer_contact_in_a_team_group_makes_it_the_customers_group(self):
		contact = _contact("Shivaani", CUSTOMER)
		deal = _deal("Mgx Clothing Co", contact)
		self.message(COLLEAGUE)
		self.assertEqual(self.group(), {"group_type": "Internal", "organization": None})

		name = self.message(CUSTOMER)
		self.assertEqual(self.group(), {"group_type": "Customer", "organization": "Mgx Clothing Co"})
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "crm_deal"), deal)
		self.assertEqual(
			frappe.db.get_value("Periskope Chat", {"chat_id": GROUP}, "organization"), "Mgx Clothing Co"
		)

	def test_our_own_people_and_unclear_members_change_nothing(self):
		_deal("Other Buyer Knits", _contact("Colleague at buyer", COLLEAGUE))
		self.assertEqual(linking.member_organizations(f"91{COLLEAGUE}"), set())
		self.message(COLLEAGUE)
		self.assertEqual(self.group().organization, None)

		contact = _contact("Two hats", CUSTOMER)
		_deal("Mgx One Spinners", contact)
		_deal("Mgx Two Weavers", contact)
		self.message(CUSTOMER)  # works for two companies: left to people
		self.assertEqual(self.group(), {"group_type": "Internal", "organization": None})

	def test_a_member_from_a_company_the_group_name_does_not_suggest_changes_nothing(self):
		# a forwarder in a team group is a contact on some customer's deal too
		_deal("Srinivasa Forwarders", _contact("Forwarder", CUSTOMER))
		self.message(CUSTOMER)
		self.assertEqual(self.group(), {"group_type": "Internal", "organization": None})

	def test_a_group_set_up_by_hand_is_left_alone(self):
		_deal("Mgx Clothing Co", _contact("Shivaani", CUSTOMER))
		self.message(COLLEAGUE)
		frappe.db.set_value("Periskope Group", GROUP, "organization", _org("Chosen Buyer"))
		self.message(CUSTOMER)
		self.assertEqual(self.group().organization, "Chosen Buyer")

	def test_existing_groups_are_mapped_by_name_or_member(self):
		self.message(CUSTOMER)  # nothing known yet
		self.assertEqual(self.group().group_type, "Internal")
		_deal("Mgx Clothing Co", _contact("Shivaani", CUSTOMER))
		self.assertEqual(linking.map_groups(), 1)
		self.assertEqual(self.group(), {"group_type": "Customer", "organization": "Mgx Clothing Co"})

		frappe.db.set_value("Periskope Group", GROUP, {"group_type": "Internal", "organization": None})
		frappe.db.set_value("Periskope Chat", {"chat_id": GROUP}, "organization", None)
		frappe.db.delete("CRM Contacts", {"parenttype": "CRM Deal"})
		_deal("Mgx Apparels")
		frappe.cache().delete_value(linking.ORGANIZATIONS_KEY)
		self.assertEqual(linking.map_groups(), 1)  # 'Mgx Apparel - Pranera' now matches by name
		self.assertEqual(self.group().group_type, "Customer")


def _customer(name):
	doc = frappe.get_doc({"doctype": "Customer", "customer_name": name})
	doc.flags.ignore_mandatory = doc.flags.ignore_links = doc.flags.ignore_validate = True
	doc.insert(ignore_permissions=True)
	return doc.name


def _address(customer, phone):
	doc = frappe.get_doc(
		{
			"doctype": "Address",
			"address_title": customer,
			"address_type": "Billing",
			"phone": phone,
			"links": [{"link_doctype": "Customer", "link_name": customer}],
		}
	)
	doc.flags.ignore_mandatory = doc.flags.ignore_links = doc.flags.ignore_validate = True
	doc.insert(ignore_permissions=True)
	return doc.name


class TestCustomerMembers(GroupCase):
	"""A customer group whose buyer's number is only on the ERPNext customer's address, and whose
	company the CRM spells a little differently."""

	def setUp(self):
		super().setUp()
		if not frappe.db.exists("DocType", "Customer"):
			self.skipTest("ERPNext is not installed")

	def test_a_number_on_the_customers_address_makes_it_the_customers_group(self):
		_deal("Mgx Clothng Co")  # misspelt in the CRM
		_address(_customer("MGX CLOTHING CO"), CUSTOMER)
		self.message(COLLEAGUE)
		self.assertEqual(self.group().group_type, "Internal")
		self.message(CUSTOMER)
		self.assertEqual(self.group(), {"group_type": "Customer", "organization": "Mgx Clothng Co"})

	def test_a_contact_of_the_customer_counts_too(self):
		_deal("Mgx Clothing Co")
		customer = _customer("MGX CLOTHING CO")
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Buyer",
				"phone_nos": [{"phone": CUSTOMER}],
				"links": [{"link_doctype": "Customer", "link_name": customer}],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(linking.member_organizations(f"91{CUSTOMER}"), {"Mgx Clothing Co"})
		self.assertTrue(contact.name)

	def test_a_customer_with_no_single_organization_changes_nothing(self):
		_deal("Mgx Clothing Coa")
		_deal("Mgx Clothing Cob")  # two spellings close to the customer's name
		_address(_customer("MGX CLOTHING CO"), CUSTOMER)
		self.assertEqual(linking.member_organizations(f"91{CUSTOMER}"), set())
		self.message(CUSTOMER)
		self.assertEqual(self.group(), {"group_type": "Internal", "organization": None})

	def test_a_number_typed_with_spaces_or_dashes_still_matches(self):
		_deal("Mgx Clothing Co")
		customer = _customer("MGX CLOTHING CO")
		_address(customer, f"{CUSTOMER[:5]} {CUSTOMER[5:]} ")
		self.assertEqual(linking.member_organizations(f"91{CUSTOMER}"), {"Mgx Clothing Co"})
		frappe.db.delete("Address", {"phone": ["like", f"{CUSTOMER[:5]}%"]})
		self.assertEqual(linking.member_organizations(f"91{CUSTOMER}"), set())
		contact = frappe.get_doc(
			{
				"doctype": "Contact",
				"first_name": "Buyer",
				"phone_nos": [{"phone": f"+91-{CUSTOMER[:5]}-{CUSTOMER[5:]}"}],
				"links": [{"link_doctype": "Customer", "link_name": customer}],
			}
		)
		contact.insert(ignore_permissions=True)
		self.message(CUSTOMER)
		self.assertEqual(self.group(), {"group_type": "Customer", "organization": "Mgx Clothing Co"})

	def test_a_longer_number_with_the_same_digits_does_not_match(self):
		_deal("Mgx Clothing Co")
		_address(_customer("MGX CLOTHING CO"), f"44{CUSTOMER}")  # a foreign number ending the same
		self.assertEqual(linking.member_organizations(f"91{CUSTOMER}"), set())

	def test_our_own_numbers_on_a_customer_address_change_nothing(self):
		_deal("Mgx Clothing Co")
		_address(_customer("MGX CLOTHING CO"), COLLEAGUE)
		self.assertEqual(linking.member_organizations(f"91{COLLEAGUE}"), set())


class TestLearnerCandidates(GroupCase):
	def test_the_ai_is_offered_the_members_and_the_closest_companies(self):
		_deal("Mgx Clothing Co", _contact("Shivaani", CUSTOMER))
		_deal("Mgx Apparels")
		self.message(COLLEAGUE)
		with patch.object(linking, "map_group_by_member"):
			self.message(CUSTOMER)
		group = frappe._dict(chat_id=GROUP, group_name="Mgx Apparel - Pranera")
		found = learner.candidate_organizations(group)
		self.assertEqual(found[0], "Mgx Clothing Co")
		self.assertIn("Mgx Apparels", found)
		listed = next(g for g in learner.snapshot()["unmapped_groups"] if g["chat_id"] == GROUP)
		self.assertEqual(listed["candidate_organizations"], found)


class TestBudget(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_budget_share_test")
		settings(enabled=1)

	def _usage(self, tokens, when=None):
		doc = frappe.get_doc(
			{"doctype": "Periskope AI Usage", "status": "OK", "input_tokens": tokens, "output_tokens": 0}
		).insert(ignore_permissions=True)
		if when:
			frappe.db.set_value("Periskope AI Usage", doc.name, "creation", when, update_modified=False)

	def test_background_work_gets_only_a_days_share(self):
		frappe.db.delete("Periskope AI Usage")
		settings(ai_monthly_budget_tokens=0)
		self._usage(10**9)
		self.assertFalse(health.daily_share_used())  # no budget set: no limit
		frappe.db.delete("Periskope AI Usage")
		settings(ai_monthly_budget_tokens=3_100_000)  # a share is at least 100,000 a day
		self._usage(50_000)
		self._usage(500_000, add_days(now_datetime(), -1) if now_datetime().day > 1 else None)
		self.assertEqual(health.daily_share_used(), now_datetime().day == 1)
		self._usage(70_000)
		self.assertTrue(health.daily_share_used())

	def test_the_catch_up_skips_old_messages_and_stops_at_the_days_share(self):
		old = frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": "budget_old",
				"chat_id": "919600000901@c.us",
				"direction": "Incoming",
				"body": "price?",
				"classification_status": "Pending",
			}
		).insert(ignore_permissions=True)
		new = frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": "budget_new",
				"chat_id": "919600000901@c.us",
				"direction": "Incoming",
				"body": "price?",
				"classification_status": "Pending",
			}
		).insert(ignore_permissions=True)
		past = add_days(now_datetime(), -(classifier.CATCH_UP_DAYS + 1))
		frappe.db.set_value(
			"Periskope Message", old.name, {"creation": past, "modified": past}, update_modified=False
		)
		earlier = add_days(now_datetime(), -1)
		frappe.db.set_value("Periskope Message", new.name, "modified", earlier, update_modified=False)
		with (
			patch.object(classifier, "_ready_to_run", return_value=True),
			patch.object(health, "budget_exceeded", return_value=False),
			patch.object(health, "daily_share_used", return_value=True),
			patch.object(classifier, "_classify_and_commit") as run,
		):
			classifier.run_batch()
		run.assert_not_called()
		self.assertEqual(
			frappe.db.get_value(
				"Periskope Message", old.name, ["classification_status", "classification_error"]
			),
			("Skipped", classifier.TOO_OLD),
		)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", new.name, "classification_status"), "Pending"
		)
		with (
			patch.object(classifier, "_ready_to_run", return_value=True),
			patch.object(health, "budget_exceeded", return_value=False),
			patch.object(health, "daily_share_used", return_value=False),
			patch.object(classifier, "_classify_and_commit") as run,
		):
			classifier.run_batch()
		self.assertIn(new.name, [c.args[0] for c in run.call_args_list])
