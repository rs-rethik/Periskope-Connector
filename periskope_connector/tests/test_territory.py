"""Territory sales reps (territory.py): a chat goes to the sales rep of its customer's territory when
nobody has it, the rep's sales persons can take it, and an assigned chat is visible on any number."""

import frappe

from periskope_connector import access, territory
from periskope_connector.api import chat_actions
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.test_crm_panel import CUSTOMER, PanelCase

REP = "pkt.rep@example.com"
MEMBER = "pkt_member@example.com"  # an underscore, as real emails have (gokul_s@...)
REGION = "_T KONGU"
DISTRICT = "_T Erode"


def _territory(name: str, manager: str | None = None, parent: str | None = None, group: int = 0):
	if not frappe.db.exists("CRM Territory", name):
		frappe.get_doc(
			{
				"doctype": "CRM Territory",
				"territory_name": name,
				"territory_manager": manager,
				"parent_crm_territory": parent,
				"is_group": group,
			}
		).insert(ignore_permissions=True)
	return name


def _employee(user: str) -> str:
	"""An Employee for the user, written directly (its many required HR fields do not matter here)."""
	name = frappe.db.get_value("Employee", {"user_id": user})
	if name:
		return name
	doc = frappe.new_doc("Employee")
	doc.update({"first_name": user.split("@")[0], "user_id": user, "status": "Active"})
	doc.name = f"_T-{frappe.generate_hash(length=6)}"
	doc.db_insert()
	return doc.name


class TerritoryCase(PanelCase):
	def setUp(self):
		if not frappe.db.exists("DocType", "CRM Territory"):
			self.skipTest("Frappe CRM is not installed")
		super().setUp()
		_user(REP, "Sales User")
		_user(MEMBER, "Sales User")
		_territory(REGION, REP, group=1)
		_territory(DISTRICT, parent=REGION)  # no manager of its own: the region's rep
		self.org = frappe.get_doc(
			{"doctype": "CRM Organization", "organization_name": "_T Erode Mills", "territory": DISTRICT}
		).insert(ignore_permissions=True)

	def _chat_of_org(self):
		chat = self._msg()
		frappe.db.set_value("Periskope Chat", chat, {"organization": self.org.name, "_assign": "[]"})
		return chat


class TestRep(TerritoryCase):
	def test_the_nearest_territory_manager_is_the_rep(self):
		self.assertEqual(territory.manager_of(DISTRICT), REP)
		self.assertEqual(territory.manager_of(REGION), REP)
		frappe.db.set_value("User", REP, "enabled", 0)
		self.assertIsNone(territory.manager_of(DISTRICT))

	def test_an_unassigned_chat_goes_to_its_rep_and_an_assigned_one_stays(self):
		chat = self._chat_of_org()
		self.assertEqual(territory.assign_rep(chat), REP)
		self.assertIn(REP, frappe.parse_json(frappe.db.get_value("Periskope Chat", chat, "_assign")))
		self.assertTrue(frappe.db.exists("Periskope Chat Activity", {"chat": chat, "action": "Territory"}))
		frappe.db.set_value("Periskope Chat", chat, "_assign", frappe.as_json(["Administrator"]))
		self.assertIsNone(territory.assign_rep(chat))  # a hand assignment is never replaced

	def test_the_deal_or_lead_territory_comes_before_the_organizations(self):
		chat = self._chat_of_org()
		other = _territory("_T ROI", "Administrator")
		lead = frappe.get_doc(
			{
				"doctype": "CRM Lead",
				"first_name": "Territory",
				"mobile_no": f"+{CUSTOMER}",
				"territory": other,
			}
		)
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Chat", chat, "crm_lead", lead.name)
		self.assertEqual(territory.territory_of(chat), other)

	def test_an_assigned_chat_is_visible_on_any_number(self):
		chat = self._chat_of_org()
		self.assertFalse(access.can_see_chat(chat, REP))  # not an agent of the number
		territory.assign_rep(chat)
		self.assertTrue(access.can_see_chat(chat, REP))

	def test_assigning_the_existing_chats_sends_no_bell(self):
		chat = self._chat_of_org()
		before = frappe.db.count("Notification Log", {"for_user": REP})
		result = territory.assign_unassigned()
		self.assertGreaterEqual(result["assigned"], 1)
		self.assertIn(REP, frappe.parse_json(frappe.db.get_value("Periskope Chat", chat, "_assign")))
		self.assertEqual(frappe.db.count("Notification Log", {"for_user": REP}), before)


class TestTeam(TerritoryCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("DocType", "Sales Person"):
			self.skipTest("ERPNext is not installed")
		head = frappe.get_doc(
			{
				"doctype": "Sales Person",
				"sales_person_name": "_T Rep Team",
				"is_group": 1,
				"employee": _employee(REP),
			}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Sales Person",
				"sales_person_name": "_T Member",
				"parent_sales_person": head.name,
				"employee": _employee(MEMBER),
			}
		).insert(ignore_permissions=True)
		frappe.local.cache.pop(f"periskope_team_of:{REP}", None)

	def test_the_reps_sales_persons_are_the_team(self):
		self.assertEqual(territory.team_of(REP), [MEMBER])
		self.assertEqual(territory.team_of(MEMBER), [])

	def test_the_rep_hands_a_chat_to_a_sales_person_and_still_sees_it(self):
		chat = self._chat_of_org()
		self.assertIn(MEMBER, chat_actions.assignable(chat))
		frappe.db.set_value("Periskope Chat", chat, "_assign", frappe.as_json([MEMBER]))
		self.assertTrue(access.can_see_chat(chat, MEMBER))
		frappe.local.cache.pop(f"periskope_team_of:{REP}", None)
		self.assertTrue(access.can_see_chat(chat, REP))  # assigned to their sales person
