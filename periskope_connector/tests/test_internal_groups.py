"""Tests for team (Internal) groups: who counts as one of our own people, choosing the customer of a
group by its members, marking a group internal by hand, keeping team talk out of the CRM, and the
AI's immediate product answers in a team group."""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import answer, autoreply, crm_context, groups, linking, messages
from periskope_connector.ai import analyse
from periskope_connector.api import crm_panel
from periskope_connector.tests.test_group_matching import _contact, _deal, _org
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

GROUP = "120363888000333@g.us"
BUYER = "8155003311"
SECOND_BUYER = "8155003322"
OTHER_BUYER = "8155003344"
STAFF = "9943003311"  # one of ours, saved on a customer, not a user or employee


class InternalCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_internal_groups_test")
		settings(enabled=1, test_mode=0)
		for p in (
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Team ~ Pranera"),
			patch.object(messages, "fetch_chat_name", lambda *a, **k: "Team ~ Pranera"),
			patch("periskope_connector.ai.classifier.queue", lambda *a, **k: None),
			patch.object(groups, "fetch_members", lambda *a, **k: []),
		):
			p.start()
			self.addCleanup(p.stop)
		frappe.db.delete("Periskope Group", {"chat_id": GROUP})
		self.addCleanup(frappe.cache().delete_value, linking.ORGANIZATIONS_KEY)
		frappe.cache().delete_value(linking.ORGANIZATIONS_KEY)
		linking.staff_numbers.clear_cache()
		self.addCleanup(linking.staff_numbers.clear_cache)
		self.n = 0

	def message(self, sender, body="Noted", name=""):
		self.n += 1
		return messages.create_message(
			event(
				message_id=f"internal_group_{self.n}",
				chat_id=GROUP,
				sender_phone=f"91{sender}@c.us",
				sender_name=name,
				body=body,
				timestamp=now_datetime().strftime("%Y-%m-%dT%H:%M:%SZ"),
			)
		)

	def group(self):
		return frappe.db.get_value(
			"Periskope Group", GROUP, ["group_type", "organization", "marked_internal"], as_dict=True
		)

	def members(self, *numbers):
		frappe.db.set_value(
			"Periskope Group", GROUP, "members", json.dumps([[f"91{n}", ""] for n in numbers])
		)


class TestOurOwnPeople(InternalCase):
	def test_a_number_in_many_groups_is_ours(self):
		_deal("Staffer Knits", _contact("Staffer", STAFF))
		for i in range(groups.OUR_GROUPS):
			frappe.get_doc(
				{
					"doctype": "Periskope Group",
					"chat_id": f"12036388800044{i}@g.us",
					"group_type": "Internal",
					"members": json.dumps([[f"91{STAFF}", ""]]),
				}
			).insert(ignore_permissions=True)
		self.assertTrue(groups.is_ours(f"91{STAFF}"))
		self.message(STAFF)
		self.assertEqual(self.group().group_type, "Internal")

	def test_a_member_named_after_our_company_is_ours(self):
		_deal("Staffer Knits", _contact("Staffer", STAFF))
		with patch.object(groups, "own_companies", lambda: ["Pranera Services and Solutions Pvt Ltd"]):
			self.assertTrue(groups.is_ours(f"91{STAFF}", "Ravi Pranera Sales"))
			self.assertFalse(groups.is_ours(f"91{STAFF}", "Ravi"))


class TestChoosingTheCustomer(InternalCase):
	def test_the_company_with_most_members_wins(self):
		_deal("Alpha Weaves", _contact("A1", BUYER))
		_deal("Alpha Weaves", _contact("A2", SECOND_BUYER))
		_deal("Beta Prints", _contact("B1", OTHER_BUYER))
		members = [[f"91{n}", ""] for n in (BUYER, SECOND_BUYER, OTHER_BUYER)]
		self.assertEqual(groups.choose_organization("Team ~ Pranera", members), "Alpha Weaves")

	def test_one_member_each_from_two_companies_stays_internal(self):
		_deal("Alpha Weaves", _contact("A1", BUYER))
		_deal("Beta Prints", _contact("B1", OTHER_BUYER))
		members = [[f"91{BUYER}", ""], [f"91{OTHER_BUYER}", ""]]
		self.assertIsNone(groups.choose_organization("Team ~ Pranera", members))
		# the group name settles it
		self.assertEqual(groups.choose_organization("Beta ~ Pranera", members), "Beta Prints")

	def test_one_person_on_two_companies_is_the_newest_one(self):
		contact = _contact("Two hats", BUYER)
		old = _deal("Gamma Old Name", contact)
		_deal("Gamma New Name", contact)
		frappe.db.set_value("CRM Deal", old, "modified", add_days(now_datetime(), -30), update_modified=False)
		self.assertEqual(groups.choose_organization("Team", [[f"91{BUYER}", ""]]), "Gamma New Name")

	def test_an_exact_customer_name_wins_over_a_shorter_one(self):
		_deal("Delta Garment")
		_deal("Delta")
		self.assertEqual(linking.organization_of_customer("DELTA GARMENT"), "Delta Garment")

	def test_a_silent_member_from_the_stored_list_counts(self):
		_deal("Alpha Weaves", _contact("A1", BUYER))
		self.message(STAFF)
		self.members(STAFF, BUYER)
		self.message(STAFF)
		self.assertEqual(self.group().organization, "Alpha Weaves")


class TestMarkedInternal(InternalCase):
	def setUp(self):
		super().setUp()
		self.message(STAFF)
		self.chat = frappe.db.get_value("Periskope Chat", {"chat_id": GROUP})

	def test_a_marked_group_is_never_matched_again(self):
		_deal("Alpha Weaves", _contact("A1", BUYER))
		groups.mark_internal(GROUP)
		self.message(BUYER)
		self.assertEqual(self.group(), {"group_type": "Internal", "organization": None, "marked_internal": 1})
		groups.map_groups()
		self.assertEqual(self.group().organization, None)

	def test_linking_an_organization_undoes_the_mark(self):
		groups.mark_internal(GROUP)
		with patch.object(crm_panel.access, "require_inbox_user"):
			crm_panel.link_organization(self.chat, _org("Alpha Weaves"))
		self.assertEqual(
			self.group(), {"group_type": "Customer", "organization": "Alpha Weaves", "marked_internal": 0}
		)

	def test_only_managers_mark_a_group(self):
		with (
			patch.object(crm_panel.access, "require_inbox_user"),
			patch.object(crm_panel.access, "is_manager", return_value=False),
		):
			self.assertRaises(frappe.PermissionError, crm_panel.mark_internal, self.chat)


class TestKeptOutOfTheCrm(InternalCase):
	def setUp(self):
		super().setUp()
		self.deal = _deal("Alpha Weaves")
		self.name = self.message(STAFF, body="Alpha Weaves wants 200 kg by Friday")
		self.chat = frappe.db.get_value("Periskope Chat", {"chat_id": GROUP})

	def test_a_message_naming_a_customer_is_not_linked(self):
		frappe.db.set_value("Periskope Message", self.name, "mentioned_customer", "Alpha Weaves")
		self.assertEqual(linking.link_message(self.name), "Internal")
		self.assertIsNone(frappe.db.get_value("Periskope Message", self.name, "crm_deal"))

	def test_no_notes_comments_leads_or_deals(self):
		frappe.db.set_value("Periskope Chat", self.chat, "crm_deal", self.deal)  # an old link
		self.assertIsNone(crm_context.record_of(GROUP))
		self.assertEqual(
			analyse.run(GROUP, force=True), {"skipped": "an internal group: kept out of the CRM"}
		)
		with patch.object(crm_panel.access, "require_inbox_user"):
			self.assertRaises(frappe.ValidationError, crm_panel.create_lead, self.chat, "Someone")
			self.assertRaises(frappe.ValidationError, crm_panel.link_record, self.chat, "CRM Deal", self.deal)

	def test_old_comments_notes_and_links_are_removed(self):
		comment = frappe.get_doc(
			{"doctype": "Comment", "comment_type": "Comment", "reference_doctype": "CRM Deal",
			 "reference_name": self.deal, "content": "from the team group"}
		).insert(ignore_permissions=True)  # fmt: skip
		note = frappe.get_doc(
			{"doctype": "FCRM Note", "title": "Summary", "reference_doctype": "CRM Deal",
			 "reference_docname": self.deal}
		).insert(ignore_permissions=True)  # fmt: skip
		frappe.get_doc(
			{"doctype": "Periskope CRM Record", "key": f"{GROUP}|summary", "kind": "Summary",
			 "chat_id": GROUP, "target_doctype": "FCRM Note", "target_name": note.name}
		).insert(ignore_permissions=True)  # fmt: skip
		frappe.db.set_value(
			"Periskope Message", self.name, {"crm_deal": self.deal, "timeline_comment": comment.name}
		)
		frappe.db.set_value("Periskope Chat", self.chat, {"crm_deal": self.deal, "summary_note": note.name})

		self.assertEqual(groups.remove_from_crm(GROUP), 1)
		self.assertFalse(frappe.db.exists("Comment", comment.name))
		self.assertFalse(frappe.db.exists("FCRM Note", note.name))
		self.assertIsNone(frappe.db.get_value("Periskope Message", self.name, "crm_deal"))
		self.assertIsNone(frappe.db.get_value("Periskope Chat", self.chat, "crm_deal"))


class TestTeamQuestions(InternalCase):
	def setUp(self):
		super().setUp()
		settings(enable_auto_reply=1, reply_delay_minutes=15, auto_reply_hours="Any time")
		frappe.db.delete("Periskope Auto Reply", {"chat_id": GROUP})
		for p in (
			patch.object(autoreply, "send_message"),
			patch.object(autoreply.assistant, "readiness", return_value=(True, "")),
			patch.object(autoreply.frappe, "enqueue"),
		):
			p.start()
			self.addCleanup(p.stop)
		self.send = autoreply.send_message

	def row(self):
		return frappe.db.get_value(
			"Periskope Auto Reply", {"chat_id": GROUP}, ["name", "status", "due_on"], as_dict=True
		)

	def ask(self, result):
		with patch.object(answer, "mentions_item", return_value=True):
			self.message(STAFF, body="Do we have 30s combed cotton in stock?")
		with patch.object(autoreply.answer, "build", return_value=result):
			return autoreply.process(self.row().name)

	def test_a_product_question_is_answered_at_once(self):
		status = self.ask(
			{
				"can_answer": True,
				"confidence": 95,
				"answer": "1,200 kg free",
				"used_sources": [],
				"holding": "",
			}
		)
		self.assertEqual(status, "Sent")
		self.assertEqual(self.send.call_args.args[1], "1,200 kg free")
		autoreply.frappe.enqueue.assert_called()  # a job answers it now, not the reply delay

	def test_when_not_sure_nothing_is_sent(self):
		status = self.ask(
			{
				"can_answer": False,
				"confidence": 20,
				"answer": "",
				"used_sources": [],
				"holding": "A rep will reply",
			}
		)
		self.assertEqual(status, "Skipped")
		self.send.assert_not_called()  # no holding message in a team group

	def test_other_talk_and_customer_groups_get_nothing(self):
		self.message(STAFF, body="Lunch at 1?")
		self.assertIsNone(self.row())
		frappe.db.set_value("Periskope Group", GROUP, "group_type", "Customer")
		with patch.object(answer, "mentions_item", return_value=True):
			self.message(STAFF, body="Do we have 30s combed cotton in stock?")
		self.assertIsNone(self.row())
