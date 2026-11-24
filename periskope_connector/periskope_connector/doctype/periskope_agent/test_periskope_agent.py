import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import access, numbers
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate, make_sales_manager

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


class TestNumberAccess(FrappeTestCase):
	"""A user given numbers works those numbers only, whatever their role, and sees every chat there."""

	def setUp(self):
		isolate(self, "periskope_number_access_test")
		self.mine = numbers.ensure("919600066601")
		self.other = numbers.ensure("919600066602")

	def _user(self, email, *roles):
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{"doctype": "User", "email": email, "first_name": "Access", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", email).add_roles(*roles)
		return email

	def _give(self, user, *numbers_):
		frappe.get_doc(
			{
				"doctype": "Periskope Agent",
				"user": user,
				"numbers": [{"whatsapp_number": n} for n in numbers_],
			}
		).insert(ignore_permissions=True)

	def _chat(self, number, contact, assign=None):
		doc = frappe.get_doc(
			{
				"doctype": "Periskope Chat",
				"chat_id": f"{contact}@c.us",
				"whatsapp_number": number,
				"org_phone": number,
				"status": "Open",
			}
		).insert(ignore_permissions=True)
		if assign:
			frappe.db.set_value("Periskope Chat", doc.name, "_assign", frappe.as_json([assign]))
		return doc.name

	def test_a_sales_manager_given_a_number_works_that_number_only(self):
		user = make_sales_manager(self._user("access.limited.rep@example.com", "Sales User", "Sales Manager"))
		self.assertIsNone(
			access.allowed_numbers(user)
		)  # a listed Sales Manager, no numbers yet: every number
		self._give(user, self.mine)
		self.assertEqual(access.allowed_numbers(user), [self.mine])
		on_mine = self._chat(self.mine, "919600077701")  # assigned to nobody
		on_other = self._chat(self.other, "919600077702")
		self.assertTrue(access.can_see_chat(on_mine, user))
		self.assertFalse(access.can_see_chat(on_other, user))
		self.assertFalse(access.chat_permission(frappe._dict(name=on_other), "read", user))

	def test_a_chat_assigned_to_them_on_another_number_still_shows(self):
		user = self._user("access.rep@example.com", "Sales User")
		self._give(user, self.mine)
		assigned = self._chat(self.other, "919600077703", assign=user)
		self.assertTrue(access.can_see_chat(assigned, user))

	def test_admins_see_every_number_even_with_numbers_given(self):
		user = self._user("access.admin@example.com", "System Manager")
		self._give(user, self.mine)
		self.assertIsNone(access.allowed_numbers(user))

	def test_several_numbers_and_a_team_add_up(self):
		user = self._user("access.multi@example.com", "Sales User")
		third = numbers.ensure("919600066603")
		self._give(user, self.mine, self.other)
		frappe.get_doc(
			{
				"doctype": "Periskope Team",
				"team_name": "Access Test Team",
				"assignment": "Manual",
				"members": [{"user": user}],
				"numbers": [{"whatsapp_number": third}],
			}
		).insert(ignore_permissions=True)
		self.assertEqual(access.allowed_numbers(user), sorted([self.mine, self.other, third]))

	def test_alerts_go_to_the_managers_who_see_the_number(self):
		limited = self._user("access.limited.manager@example.com", "Sales Manager")
		self._give(limited, self.mine)
		managers = {limited: access.allowed_numbers(limited), "everyone@example.com": None}
		self.assertEqual(sorted(access.managers_of(self.mine, managers)), sorted(managers))
		self.assertEqual(access.managers_of(self.other, managers), ["everyone@example.com"])
