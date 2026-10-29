"""Tests for the CRM panel of the WhatsApp inbox (phase c): what it shows about a chat's lead or deal,
linking by hand (kept over the match by number, on every number of the contact), creating a lead,
and notes, tasks and status changed through the CRM's own permissions."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import chat, messages, mirror
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

	def test_the_crm_tab_shows_which_number_a_message_came_on(self):
		frappe.get_doc({"doctype": "WhatsApp Number", "number": B, "display_name": "Sales desk"}).insert(
			ignore_permissions=True
		)
		self.assertEqual(
			mirror._label(frappe._dict(agent_reply="Answer", org_phone=B)), "AI reply · via Sales desk"
		)


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
