"""Tests for the CRM panel of the WhatsApp inbox (phase c): what it shows about a chat's lead or deal,
linking by hand (kept over the match by number, on every number of the contact), creating a lead,
and notes, tasks and status changed through the CRM's own permissions."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import chat, messages
from periskope_connector.api import crm_panel
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

A = "919600011111"
B = "919600022222"
CUSTOMER = "919600033333"


def _lead(first_name, mobile=None, owner="Administrator"):
	doc = frappe.get_doc(
		{
			"doctype": "CRM Lead",
			"first_name": first_name,
			"mobile_no": f"+{mobile}" if mobile else None,
			"lead_owner": owner,
		}
	)
	doc.flags.ignore_mandatory = True
	return doc.insert(ignore_permissions=True).name


class PanelCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_panel_test")
		for p in (
			patch.object(messages, "fetch_chat_name", lambda *a, **k: ""),
			patch("periskope_connector.autoreply.on_message", lambda doc: ""),
		):
			p.start()
			self.addCleanup(p.stop)
		self.addCleanup(frappe.set_user, "Administrator")
		self.n = 0

	def _msg(self, number=A, contact=CUSTOMER, body="Need 500 kg of 30s cotton"):
		self.n += 1
		messages.create_message(
			event(
				message_id=f"false_{contact}@c.us_P{self.n}{frappe.generate_hash(length=4)}",
				chat_id=f"{contact}@c.us",
				sender_phone=f"{contact}@c.us",
				org_phone=f"{number}@c.us",
				body=body,
			)
		)
		return chat.name_of(number, f"{contact}@c.us")


class TestContext(PanelCase):
	def test_the_panel_shows_the_lead_its_tasks_notes_and_other_numbers(self):
		lead = _lead("Ravi", CUSTOMER)
		on_a = self._msg(A)
		on_b = self._msg(B)
		frappe.get_doc(
			{
				"doctype": "CRM Task",
				"title": "Send rate card",
				"status": "Todo",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		).insert(ignore_permissions=True)
		ctx = crm_panel.get_context(on_a)
		record = ctx["record"]
		self.assertEqual(
			(record["doctype"], record["name"], record["route"]), ("CRM Lead", lead, f"/crm/leads/{lead}")
		)
		self.assertTrue(record["statuses"])
		self.assertEqual([t.title for t in record["tasks"]], ["Send rate card"])
		self.assertEqual([o["name"] for o in ctx["other_chats"]], [on_b])

	def test_the_summary_keeps_its_lines_and_is_not_listed_twice(self):
		lead = _lead("Summary", CUSTOMER)
		on_a = self._msg(A)
		note = frappe.get_doc(
			{
				"doctype": "FCRM Note",
				"title": "WhatsApp summary",
				"content": "<p><b>What the customer wants</b></p><p>Price for 500 kg</p>",
				"reference_doctype": "CRM Lead",
				"reference_docname": lead,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Chat", on_a, "summary_note", note.name)
		record = crm_panel.get_context(on_a)["record"]
		self.assertEqual(record["summary"], "What the customer wants\nPrice for 500 kg")
		self.assertNotIn(note.name, [n.name for n in record["notes"]])

	def test_an_unlinked_chat_offers_to_link_or_create(self):
		ctx = crm_panel.get_context(self._msg(A, "919600099999"))
		self.assertIsNone(ctx["record"])


class TestLinking(PanelCase):
	def test_a_link_by_hand_covers_every_number_and_beats_the_number_match(self):
		by_number = _lead("By number", CUSTOMER)
		chosen = _lead("Chosen")
		on_a = self._msg(A)
		on_b = self._msg(B)
		self.assertEqual(frappe.db.get_value("Periskope Chat", on_a, "crm_lead"), by_number)
		crm_panel.link_record(on_a, "CRM Lead", chosen)
		for name in (on_a, on_b):
			self.assertEqual(
				frappe.db.get_value("Periskope Chat", name, ["crm_lead", "linked_by_hand"]), (chosen, 1)
			)
		self.assertEqual(
			set(
				frappe.get_all("Periskope Message", filters={"chat_id": f"{CUSTOMER}@c.us"}, pluck="crm_lead")
			),
			{chosen},
		)
		# A new message still goes to the chosen lead, not the one with the same number.
		self._msg(A, body="Any update?")
		latest = frappe.get_all(
			"Periskope Message",
			filters={"chat_id": f"{CUSTOMER}@c.us"},
			pluck="crm_lead",
			order_by="creation desc",
			limit=1,
		)
		self.assertEqual(latest, [chosen])
		# Undo: back to the match by number.
		crm_panel.unlink_record(on_a)
		self.assertEqual(
			frappe.db.get_value("Periskope Chat", on_a, ["crm_lead", "linked_by_hand"]), (by_number, 0)
		)

	def test_a_deal_keeps_its_lead(self):
		lead = _lead("Deal lead")
		deal = frappe.get_doc({"doctype": "CRM Deal", "lead": lead, "deal_owner": "Administrator"})
		deal.flags.ignore_mandatory = True
		deal.insert(ignore_permissions=True)
		on_a = self._msg(A, "919600088888")
		crm_panel.link_record(on_a, "CRM Deal", deal.name)
		self.assertEqual(
			frappe.db.get_value("Periskope Chat", on_a, ["crm_deal", "crm_lead"]), (deal.name, lead)
		)
		self.assertEqual(crm_panel.chats_for_record("CRM Deal", deal.name)[0]["name"], on_a)

	def test_only_records_the_crm_lets_you_read(self):
		on_a = self._msg(A, "919600077777")
		lead = _lead("Someone else's")
		with patch.object(frappe, "has_permission", lambda *a, **k: False):
			self.assertRaises(frappe.PermissionError, crm_panel.link_record, on_a, "CRM Lead", lead)
		self.assertRaises(frappe.PermissionError, crm_panel.link_record, on_a, "ToDo", "x")


class TestSearch(PanelCase):
	def test_every_word_counts_and_a_number_matches_in_any_format(self):
		lead = _lead("Zubairqx", mobile="919600044444")
		frappe.db.set_value("CRM Lead", lead, "organization", None)
		names = lambda q: [r["name"] for r in crm_panel.search_records(q)]  # noqa: E731
		# the chat's full name finds the lead saved under the first name only
		self.assertIn(lead, names("Zubairqx Maheswaranqx"))
		# a number typed without "+" or with spaces still finds it
		self.assertIn(lead, names("919600044444"))
		self.assertIn(lead, names("96000 44444"))
		self.assertNotIn(lead, names("Nobodyqx"))


class TestCreateAndChange(PanelCase):
	def test_a_rep_creates_the_lead_and_owns_it(self):
		rep = "panel.rep@example.com"
		if not frappe.db.exists("User", rep):
			frappe.get_doc(
				{"doctype": "User", "email": rep, "first_name": "Rep", "send_welcome_email": 0}
			).insert(ignore_permissions=True)
		frappe.get_doc("User", rep).add_roles("Sales User")
		on_a = self._msg(A, "919600066666")
		frappe.get_doc(
			{"doctype": "Periskope Agent", "user": rep, "numbers": [{"whatsapp_number": A}]}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Chat", on_a, "_assign", frappe.as_json([rep]))
		frappe.set_user(rep)
		ctx = crm_panel.create_lead(on_a, "Meena", "K", organization="Meena Textiles")
		lead = frappe.get_doc("CRM Lead", ctx["record"]["name"])
		self.assertEqual((lead.lead_owner, lead.mobile_no, lead.source), (rep, "+919600066666", "WhatsApp"))

	def test_notes_tasks_and_status_go_on_the_linked_record(self):
		lead = _lead("Status", CUSTOMER)
		on_a = self._msg(A)
		crm_panel.add_note(on_a, "Wants 500 kg\nby Friday", title="Order")
		ctx = crm_panel.add_task(on_a, "Call back", due_date="2031-01-05", priority="High")
		task = ctx["record"]["tasks"][0]
		self.assertEqual((task.title, task.priority), ("Call back", "High"))
		self.assertEqual(ctx["record"]["notes"][0].title, "Order")
		self.assertIn("<br>", ctx["record"]["notes"][0].content)
		open_status = next(s for s in crm_panel.statuses("CRM Lead") if s["type"] != "Lost")["value"]
		self.assertEqual(crm_panel.set_status(on_a, open_status)["record"]["status"], open_status)
		lost = next((s["value"] for s in crm_panel.statuses("CRM Lead") if s["type"] == "Lost"), None)
		if lost:
			self.assertRaises(frappe.ValidationError, crm_panel.set_status, on_a, lost)  # no reason
			ctx = crm_panel.set_status(on_a, lost, lost_reason="Other", lost_notes="Bought elsewhere")
			self.assertEqual(ctx["record"]["status"], lost)
		self.assertRaises(frappe.ValidationError, crm_panel.set_status, on_a, "No such status")
		done = crm_panel.complete_task(on_a, task.name)
		self.assertEqual(done["record"]["tasks"], [])
		self.assertEqual(frappe.db.get_value("CRM Task", task.name, "status"), "Done")
		self.assertTrue(lead)

	def test_a_task_is_rescheduled_only_on_its_own_record(self):
		_lead("Resched", CUSTOMER)
		on_a = self._msg(A)
		task = crm_panel.add_task(on_a, "Send swatches", due_date="2031-01-05 10:00:00")["record"]["tasks"][0]
		ctx = crm_panel.reschedule_task(on_a, task.name, "2031-01-07T16:30")
		self.assertEqual(str(ctx["record"]["tasks"][0].due_date), "2031-01-07 16:30:00")
		self.assertRaises(frappe.ValidationError, crm_panel.reschedule_task, on_a, task.name, "")
		other = frappe.get_doc(
			{"doctype": "CRM Task", "title": "Elsewhere", "status": "Todo", "assigned_to": "Administrator"}
		).insert(ignore_permissions=True)
		self.assertRaises(frappe.PermissionError, crm_panel.reschedule_task, on_a, other.name, "2031-01-08")

	def test_changes_need_a_linked_record(self):
		on_a = self._msg(A, "919600055555")
		self.assertRaises(frappe.ValidationError, crm_panel.add_note, on_a, "hello")


def _deal(lead, status=None):
	doc = frappe.get_doc({"doctype": "CRM Deal", "lead": lead, "deal_owner": "Administrator"})
	if status:
		doc.status = status
	doc.flags.ignore_mandatory = True
	doc.flags.ignore_links = True
	return doc.insert(ignore_permissions=True).name


class TestFullView(PanelCase):
	def test_a_lead_chat_shows_the_lead_and_all_its_deals(self):
		lead = _lead("Full", CUSTOMER)
		first, second = _deal(lead), _deal(lead)
		view = crm_panel.full_view(self._msg(A))
		self.assertEqual(view["lead"].name, lead)
		self.assertEqual({d.name for d in view["deals"]}, {first, second})
		self.assertTrue(all(d.route.startswith("/crm/deals/") for d in view["deals"]))
		self.assertEqual(view["quotations"], [])
		self.assertEqual(view["orders"], [])

	def test_a_deal_chat_shows_its_lead_and_the_other_deals(self):
		lead = _lead("Deal side")
		first, second = _deal(lead), _deal(lead)
		on_a = self._msg(A, "919600077777")
		crm_panel.link_record(on_a, "CRM Deal", first)
		view = crm_panel.full_view(on_a)
		self.assertEqual(view["record"], {"doctype": "CRM Deal", "name": first})
		self.assertEqual(view["lead"].name, lead)
		self.assertEqual({d.name for d in view["deals"]}, {first, second})

	def test_an_unlinked_chat_has_no_full_view(self):
		self.assertRaises(frappe.ValidationError, crm_panel.full_view, self._msg(A, "919600088888"))


class TestDealFromMessage(PanelCase):
	def _message(self, chat_name):
		return frappe.get_all(
			"Periskope Message",
			filters={"periskope_chat": chat_name},
			pluck="name",
			order_by="message_timestamp desc",
			limit=1,
		)[0]

	def test_a_message_becomes_a_deal_that_keeps_it_and_the_lead_stays(self):
		from periskope_connector.api import inbox

		lead = _lead("Direct", CUSTOMER)
		on_a = self._msg(A, body="Need 800 kg of 40s combed, delivery by month end")
		message = self._message(on_a)
		defaults = crm_panel.deal_defaults(on_a)
		self.assertEqual((defaults["lead"], defaults["first_name"]), (lead, "Direct"))
		ctx = crm_panel.create_deal_from_message(
			on_a,
			message,
			status=defaults["status"],
			organization="Direct Mills",
			expected_closure_date="2031-02-01",
			deal_value=245000,
			note="Repeat order",
		)
		deal = frappe.get_doc("CRM Deal", ctx["created_deal"]["name"])
		self.assertEqual((deal.lead, deal.periskope_message), (lead, message))
		self.assertEqual(
			frappe.db.get_value("CRM Organization", deal.organization, "organization_name"), "Direct Mills"
		)
		self.assertTrue(deal.contacts)
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "converted"), 0)
		note = frappe.db.get_value(
			"FCRM Note", {"reference_doctype": "CRM Deal", "reference_docname": deal.name}, "content"
		)
		self.assertIn("800 kg of 40s combed", note)
		self.assertIn("Repeat order", note)
		self.assertEqual(frappe.db.get_value("Periskope Chat", on_a, "crm_deal"), deal.name)
		marked = {m.name: m.deals for m in inbox.get_messages(on_a)["messages"]}
		self.assertEqual(marked[message], [deal.name])

	def test_a_chat_with_a_deal_gets_another_one(self):
		_lead("Repeat", CUSTOMER)
		on_a = self._msg(A)
		message = self._message(on_a)
		first = crm_panel.create_deal_from_message(on_a, message)["created_deal"]["name"]
		second = crm_panel.create_deal_from_message(on_a, message)["created_deal"]["name"]
		self.assertNotEqual(first, second)
		self.assertEqual(frappe.db.get_value("Periskope Chat", on_a, "crm_deal"), second)

	def test_a_message_of_another_chat_is_refused(self):
		on_a = self._msg(A)
		other = self._msg(A, "919600099999")
		self.assertRaises(
			frappe.PermissionError, crm_panel.create_deal_from_message, on_a, self._message(other)
		)


class TestManyRecords(PanelCase):
	def _ours(self, contact=CUSTOMER, body="Price is 245 per kg"):
		self.n += 1
		return messages.create_message(
			event(
				message_id=f"true_{contact}@c.us_O{self.n}{frappe.generate_hash(length=4)}",
				chat_id=f"{contact}@c.us",
				sender_phone=f"{A}@c.us",
				org_phone=f"{A}@c.us",
				from_me=True,
				body=body,
			)
		)

	def test_a_deal_is_made_only_from_the_customers_message(self):
		_lead("Ours", CUSTOMER)
		on_a = self._msg(A)
		ours = self._ours()
		self.assertRaises(frappe.ValidationError, crm_panel.create_deal_from_message, on_a, ours)
		self.assertRaises(frappe.ValidationError, crm_panel.deal_defaults, on_a, ours)

	def test_every_lead_and_deal_of_the_contact_is_listed_the_followed_one_first(self):
		first_lead = _lead("Many", CUSTOMER)
		second_lead = _lead("Many again", CUSTOMER)
		deal = _deal(first_lead)
		on_a = self._msg(A)
		crm_panel.link_record(on_a, "CRM Lead", second_lead)
		related = crm_panel.get_context(on_a)["related"]
		names = [r["name"] for r in related]
		self.assertEqual(names[0], second_lead)
		self.assertTrue(related[0]["active"])
		self.assertTrue({first_lead, second_lead, deal} <= set(names))
		# following another one moves the chat
		ctx = crm_panel.link_record(on_a, "CRM Deal", deal)
		self.assertEqual(ctx["related"][0]["name"], deal)

	def test_in_a_group_the_deal_is_for_the_sender(self):
		sender = "919600044444"
		lead = _lead("Group buyer", sender)
		self.n += 1
		messages.create_message(
			event(
				message_id=f"false_120363000000000001@g.us_G{self.n}{frappe.generate_hash(length=4)}",
				chat_id="120363000000000001@g.us",
				sender_phone=f"{sender}@c.us",
				org_phone=f"{A}@c.us",
				body="Please book 300 kg of 30s",
				is_group=True,
			)
		)
		group_chat = chat.name_of(A, "120363000000000001@g.us")
		message = frappe.get_all(
			"Periskope Message", filters={"periskope_chat": group_chat}, pluck="name", limit=1
		)[0]
		self.assertEqual(crm_panel.deal_defaults(group_chat, message)["lead"], lead)
		ctx = crm_panel.create_deal_from_message(group_chat, message)
		deal = frappe.get_doc("CRM Deal", ctx["created_deal"]["name"])
		self.assertEqual(deal.lead, lead)
		contact_phones = frappe.get_all(
			"Contact Phone", filters={"parent": deal.contacts[0].contact}, pluck="phone"
		)
		self.assertIn(f"+{sender}", contact_phones)


class TestGroupOrganization(PanelCase):
	"""A WhatsApp group linked to a CRM organization follows the company's deal (or lead), its messages
	go to that record, and its other groups are listed."""

	GROUP = "120363000000000077@g.us"

	def _group_msg(self, body="Dispatch the 30s order", group=None):
		self.n += 1
		group = group or self.GROUP
		messages.create_message(
			event(
				message_id=f"false_{group}_O{self.n}{frappe.generate_hash(length=4)}",
				chat_id=group,
				sender_phone="919600055555@c.us",
				org_phone=f"{A}@c.us",
				body=body,
				is_group=True,
			)
		)
		return chat.name_of(A, group)

	def _org(self, name):
		if not frappe.db.exists("CRM Organization", name):
			frappe.get_doc({"doctype": "CRM Organization", "organization_name": name}).insert(
				ignore_permissions=True
			)
		return name

	def test_link_and_remove(self):
		org = self._org("Zzg Spinners")
		lead = _lead("Zzg buyer")
		frappe.db.set_value("CRM Lead", lead, "organization", org)
		deal = _deal(lead)
		frappe.db.set_value("CRM Deal", deal, "organization", org)
		group_chat = self._group_msg()

		self.assertIn(org, [o["name"] for o in crm_panel.search_organizations("Zzg")])
		ctx = crm_panel.link_organization(group_chat, org)
		self.assertEqual(ctx["organization"]["name"], org)
		self.assertEqual(ctx["organization"]["deals"], 1)
		self.assertEqual(ctx["record"]["name"], deal)  # the group follows the company's deal
		group = frappe.db.get_value(
			"Periskope Group", {"chat_id": self.GROUP}, ["organization", "group_type"], as_dict=True
		)
		self.assertEqual((group.organization, group.group_type), (org, "Customer"))
		linked = frappe.get_all(
			"Periskope Message", filters={"chat_id": self.GROUP}, fields=["crm_deal", "link_method"]
		)
		self.assertTrue(linked and all(m.crm_deal == deal for m in linked))
		self.assertEqual(frappe.db.get_value("Periskope Chat", group_chat, "organization"), org)

		# a second group of the same company is listed on the first
		other = self._group_msg(group="120363000000000078@g.us")
		crm_panel.link_organization(other, org)
		self.assertEqual(len(crm_panel.get_context(group_chat)["organization"]["groups"]), 1)

		ctx = crm_panel.unlink_organization(group_chat)
		self.assertIsNone(ctx["organization"])
		self.assertFalse(frappe.db.get_value("Periskope Group", {"chat_id": self.GROUP}, "organization"))

	def test_only_groups_and_known_organizations(self):
		one_to_one = self._msg(A)
		self.assertRaises(frappe.ValidationError, crm_panel.link_organization, one_to_one, "Anything")
		group_chat = self._group_msg()
		self.assertRaises(frappe.PermissionError, crm_panel.link_organization, group_chat, "No Such Org Zzg")

	def test_groups_page_maps_to_an_organization(self):
		from periskope_connector.api import desk_pages

		org = self._org("Zzg Weavers")
		self._group_msg()
		group = frappe.db.get_value("Periskope Group", {"chat_id": self.GROUP})
		desk_pages.map_group(group, organization=org)
		self.assertEqual(frappe.db.get_value("Periskope Group", group, "organization"), org)
		self.assertEqual(frappe.db.get_value("Periskope Chat", {"chat_id": self.GROUP}, "organization"), org)


class TestGroupTabsAndRecords(TestGroupOrganization):
	def test_internal_groups_have_their_own_tab(self):
		from periskope_connector.api import inbox

		self._group_msg()
		frappe.db.set_value("Periskope Group", {"chat_id": self.GROUP}, "group_type", "Internal")
		ids = lambda f: {c.chat_id for c in inbox.get_chats(filters=f, page_length=500)["chats"]}  # noqa: E731
		self.assertIn(self.GROUP, ids({"internal": 1}))
		self.assertNotIn(self.GROUP, ids({"internal": 0}))
		self.assertNotIn(self.GROUP, ids({"groups": 1, "internal": 0}))
		frappe.db.set_value("Periskope Group", {"chat_id": self.GROUP}, "group_type", "Customer")
		self.assertIn(self.GROUP, ids({"groups": 1, "internal": 0}))

	def test_a_linked_group_lists_only_its_organizations_records(self):
		org = self._org("Zzg Knits")
		ours = _lead("Zzg knit buyer")
		frappe.db.set_value("CRM Lead", ours, "organization", org)
		other = _lead("Someone else")
		group_chat = self._group_msg()
		crm_panel.link_record(group_chat, "CRM Lead", other)  # linked by hand to another company
		ctx = crm_panel.link_organization(group_chat, org)
		names = {r["name"] for r in ctx["related"]}
		self.assertIn(ours, names)
		self.assertNotIn(other, names)
