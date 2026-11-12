"""Regression tests for the medium-priority review (M1-M14). Periskope, the AI, push services and email
are never called for real."""

import datetime
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.utils import add_days, add_to_date, now_datetime, nowdate

from periskope_connector import autoreply, calls, chat, linking, push, reminders, writeback
from periskope_connector.ai import analyse
from periskope_connector.api import ai_review, compose, inbox, privacy, reports
from periskope_connector.api import sales_order as so
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.test_crm_panel import CUSTOMER, A, PanelCase
from periskope_connector.tests.test_sales_order import CUSTOMER_NAME, ITEM, SalesOrderCase
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = [
	*OTHER_APP_DOCTYPES,
	"Customer",
	"Item",
	"Sales Order",
	"Company",
]


def _message_of(chat_name):
	return frappe.db.get_value("Periskope Message", {"periskope_chat": chat_name, "direction": "Incoming"})


# --- M14: knowledge import reads only the user's own upload ----------------------------------------


class TestKnowledgeImportFiles(PanelCase):
	def test_a_manager_cannot_read_someone_elses_private_file(self):
		from periskope_connector import knowledge_canvas

		secret = frappe.get_doc(
			{"doctype": "File", "file_name": "payslip.csv", "content": "Salary,90000\n", "is_private": 1}
		).insert(ignore_permissions=True)
		manager = _user("m14.manager@example.com", "WhatsApp Manager")
		frappe.set_user(manager)
		with self.assertRaises(frappe.PermissionError):
			knowledge_canvas.document_text(secret.file_url)
		own = frappe.get_doc(
			{"doctype": "File", "file_name": "prices.csv", "content": "30s cotton,245\n", "is_private": 1}
		).insert(ignore_permissions=True)
		self.assertIn("30s cotton,245", knowledge_canvas.document_text(own.file_url))


# --- M5: bells reach the bell and the devices -------------------------------------------------------


class TestNotifications(PanelCase):
	def test_a_bell_about_a_message_points_at_its_chat(self):
		chat_name = self._msg()
		self.assertEqual(
			reminders._bell_target(_message_of(chat_name)),
			{"document_type": "Periskope Chat", "document_name": chat_name},
		)

	def test_a_chat_bell_is_pushed_only_to_subscribed_users_outside_quiet_hours(self):
		user = _user("m5.rep@example.com", "Sales User")
		log = frappe._dict(name="NL1", document_type="Periskope Chat", for_user=user)
		with patch.object(push.frappe, "enqueue") as enqueue:
			push.on_notification(log)
			enqueue.assert_not_called()  # no device yet
			push.subscribe(
				user, {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
			)
			push.on_notification(log)
			enqueue.assert_called_once()
			push.on_notification(frappe._dict(name="NL2", document_type="Periskope Message", for_user=user))
			enqueue.assert_called_once()
		quiet = frappe._dict(quiet_from="21:00:00", quiet_to="08:00:00")
		self.assertTrue(push.quiet_now(quiet, datetime.datetime(2031, 1, 1, 23, 0)))
		self.assertTrue(push.quiet_now(quiet, datetime.datetime(2031, 1, 1, 7, 0)))
		self.assertFalse(push.quiet_now(quiet, datetime.datetime(2031, 1, 1, 12, 0)))


# --- M6: replies stuck on "Sending" ----------------------------------------------------------------


class TestStaleSending(PanelCase):
	def test_a_reply_stuck_sending_becomes_failed(self):
		chat_name = self._msg()
		row = frappe.db.get_value("Periskope Chat", chat_name, ["chat_id", "whatsapp_number"], as_dict=True)
		doc = frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": f"local_{frappe.generate_hash(length=8)}",
				"direction": "Outgoing",
				"body": "Rate is 240",
				"chat_id": row.chat_id,
				"periskope_chat": chat_name,
				"send_status": "Sending",
				"message_timestamp": now_datetime(),
			}
		).insert(ignore_permissions=True)
		self.assertEqual(compose.fail_stale_sending(), 0)  # still in time
		frappe.db.set_value(
			"Periskope Message",
			doc.name,
			"modified",
			add_to_date(now_datetime(), minutes=-30),
			update_modified=False,
		)
		self.assertEqual(compose.fail_stale_sending(), 1)
		self.assertEqual(frappe.db.get_value("Periskope Message", doc.name, "send_status"), "Failed")


# --- M2: linking lifecycle --------------------------------------------------------------------------


class TestLinking(PanelCase):
	def _lead(self, mobile, phone=None):
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "M2", "mobile_no": mobile, "phone": phone}
		)
		lead.flags.ignore_mandatory = True
		return lead.insert(ignore_permissions=True).name

	def test_a_second_number_on_a_lead_is_matched(self):
		lead = self._lead("+919600099001", "+919600099002")
		self.assertEqual(frappe.db.get_value("CRM Lead", lead, "periskope_phone2_e164"), "919600099002")
		self.assertIn((lead, None), linking.by_mobile("919600099002"))

	def test_linking_a_contact_keeps_the_leads_own_number(self):
		from periskope_connector import contacts

		lead = self._lead("+919600099011")
		contacts._add_number("CRM Lead", lead, "919600099012")
		self.assertEqual(
			frappe.db.get_value("CRM Lead", lead, ["mobile_no", "phone"]), ("+919600099011", "+919600099012")
		)

	def test_a_number_added_in_the_crm_clears_its_contact_queue_row(self):
		frappe.get_doc({"doctype": "Periskope Contact", "phone": "919600099021", "status": "New"}).insert(
			ignore_permissions=True
		)
		lead = self._lead("+919600099021")
		linking.relink_number("919600099021")
		self.assertEqual(
			frappe.db.get_value("Periskope Contact", {"phone": "919600099021"}, ["status", "linked_lead"]),
			("Linked", lead),
		)

	def test_an_ambiguous_link_is_flagged_until_a_person_confirms_it(self):
		chat_name = self._msg()
		message = frappe.db.get_value(
			"Periskope Message", _message_of(chat_name), linking.CHAT_FIELDS, as_dict=True
		)
		message.link_status = "Ambiguous"
		chat.ensure(message)
		self.assertEqual(frappe.db.get_value("Periskope Chat", chat_name, "link_review"), 1)
		self.assertIn(chat_name, [c.name for c in inbox.get_chats({"review": 1})["chats"]])
		lead = self._lead("+919600099031")
		from periskope_connector.api import crm_panel

		crm_panel._relink(
			frappe._dict(name=chat_name, chat_id=message.chat_id, is_group=0), lead, None, by_hand=True
		)
		self.assertEqual(frappe.db.get_value("Periskope Chat", chat_name, "link_review"), 0)


# --- M13: the AI does not overwrite a person's edit -------------------------------------------------


class TestWriteback(PanelCase):
	def test_a_note_a_rep_edited_is_kept(self):
		chat_name = self._msg()
		chat_id = frappe.db.get_value("Periskope Chat", chat_name, "chat_id")
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "M13"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		settings(test_mode=0)
		with patch.object(writeback.crm_context, "record_of", return_value=("CRM Lead", lead.name)):
			note = writeback.upsert_note(chat_id, "requirement", "Needs 500 kg of 30s cotton.")
			frappe.db.set_value("FCRM Note", note, "content", "<p>Rep: 600 kg now.</p>")
			frappe.db.set_value(
				"FCRM Note", note, "modified", add_to_date(now_datetime(), minutes=5), update_modified=False
			)
			writeback.upsert_note(chat_id, "requirement", "Needs 550 kg of 30s cotton.")
		self.assertEqual(frappe.db.get_value("FCRM Note", note, "content"), "<p>Rep: 600 kg now.</p>")

	def test_one_failing_write_does_not_stop_the_others(self):
		with patch.object(frappe, "log_error"):
			self.assertIsNone(analyse._step("c", "summary", lambda: 1 / 0))
		self.assertEqual(analyse._step("c", "note", lambda: "ok"), "ok")


# --- M9: auto reply -------------------------------------------------------------------------------


class TestAutoReply(PanelCase):
	def test_a_night_message_is_answered_in_the_morning(self):
		cfg = reminders.get_config()
		night = datetime.datetime.combine(datetime.date(2031, 9, 2), datetime.time(23, 0))  # a Tuesday
		cfg.days = reminders.WEEKDAYS["Monday to Saturday"]
		self.assertEqual(
			reminders.next_working_moment(night, cfg),
			datetime.datetime.combine(datetime.date(2031, 9, 3), cfg.start),
		)
		s = frappe._dict(reply_delay_minutes=15, auto_reply_hours="Any time")
		s.get = lambda k, d=None: s[k] if k in s else d
		self.assertEqual(autoreply.due_time(s, night), night + datetime.timedelta(minutes=15))

	def test_the_fallback_is_in_english_and_tamil_unless_set(self):
		s = frappe._dict(holding_message="")
		s.get = lambda k, d=None: s[k] if k in s else d
		self.assertIn("தொடர்பு", autoreply.holding_text(s))
		s.holding_message = "Custom text"
		self.assertEqual(autoreply.holding_text(s), "Custom text")

	def test_stop_is_not_answered(self):
		from periskope_connector.api.broadcast import is_stop

		self.assertTrue(is_stop("STOP"))
		self.assertTrue(is_stop("  Unsubscribe! "))
		self.assertFalse(is_stop("stop the order please"))


# --- M1: search -------------------------------------------------------------------------------------


class TestSearch(PanelCase):
	def test_words_become_a_full_text_query_and_a_snippet(self):
		self.assertEqual(inbox.fulltext_words("Price of 30s cotton?"), "+price* +30s* +cotton*")
		self.assertEqual(inbox.fulltext_words("ok"), "")
		self.assertEqual(inbox.search_messages("ok"), [])
		snip = inbox._snippet("A" * 200 + " the cotton price is 245 " + "B" * 200, "cotton")
		self.assertIn("cotton price", snip)
		self.assertTrue(snip.startswith("…"))

	def test_another_reps_chat_is_not_searched(self):
		chat_name = self._msg()
		rep = _user("m1.rep@example.com", "Sales User")
		frappe.set_user(rep)
		with self.assertRaises(frappe.PermissionError):
			inbox.search_messages("cotton", chat=chat_name)


# --- M10: correcting the AI -------------------------------------------------------------------------


class TestAiReview(PanelCase):
	def test_a_manager_marks_an_escalation_wrong_and_removes_a_wrong_lead(self):
		chat_name = self._msg()
		message = _message_of(chat_name)
		lead = frappe.get_doc(
			{"doctype": "CRM Lead", "first_name": "Not a buyer", "mobile_no": f"+{CUSTOMER}"}
		)
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Message", message, {"ai_lead": lead.name, "escalation": "Managers"})
		out = ai_review.correct(message, escalation="Wrong", note="Just a supplier")
		self.assertEqual((out["escalation_feedback"], out["ai_feedback_note"]), ("Wrong", "Just a supplier"))
		ai_review.remove_ai_lead(message)
		self.assertFalse(frappe.db.exists("CRM Lead", lead.name))
		rep = _user("m10.rep@example.com", "Sales User")
		frappe.set_user(rep)
		with self.assertRaises(frappe.PermissionError):
			ai_review.correct(message, escalation="Right")


# --- M4: groups, calls, stickers --------------------------------------------------------------------


class TestGroupsAndCalls(PanelCase):
	def test_a_renamed_group_takes_its_new_name(self):
		gid = "120363777000111@g.us"
		frappe.db.delete("Periskope Group", {"chat_id": gid})
		frappe.get_doc(
			{"doctype": "Periskope Group", "chat_id": gid, "group_name": "Old name", "group_type": "Internal"}
		).insert(ignore_permissions=True)
		with patch.object(linking.frappe, "enqueue"):
			self.assertTrue(linking.refresh_group_name(gid, "New name"))
			self.assertFalse(linking.refresh_group_name(gid, "New name"))
		self.assertEqual(frappe.db.get_value("Periskope Group", {"chat_id": gid}, "group_name"), "New name")

	def test_a_group_call_joins_the_groups_chat_and_a_hidden_caller_none(self):
		with patch.object(calls.chat, "name_of", return_value="GROUPCHAT"):
			self.assertEqual(calls._chat_of(A, "120363000@g.us", "Incoming", {}), "GROUPCHAT")
		self.assertIsNone(calls._chat_of(A, "1234@lid", "Incoming", {}))


# --- M8: reports ------------------------------------------------------------------------------------


class TestReports(PanelCase):
	def test_the_reports_page_runs_the_new_reports(self):
		names = [r["name"] for r in reports.reports()]
		self.assertIn("Periskope WhatsApp Funnel", names)
		out = reports.run("Periskope Auto Reply Quality", str(add_days(nowdate(), -7)), nowdate())
		self.assertIn("answered_rate", [c["fieldname"] for c in out["columns"]])
		reports.run("Periskope WhatsApp Funnel", str(add_days(nowdate(), -7)), nowdate())
		frappe.set_user(_user("m8.rep@example.com", "Sales User"))
		with self.assertRaises(frappe.PermissionError):
			reports.run("Periskope WhatsApp Funnel")


# --- M3 / M11: orders and quotations ----------------------------------------------------------------


class TestOrders(SalesOrderCase):
	def test_a_quotation_is_made_and_a_missing_price_is_warned_about(self):
		if not frappe.db.get_single_value("Global Defaults", "default_company"):
			self.skipTest("No default company on this site")
		made = so.create_quotation_from_message(
			self.chat, self.message, CUSTOMER_NAME, [{"item_code": ITEM, "qty": 10, "rate": 0}]
		)
		self.assertEqual(made["doctype"], "Quotation")
		self.assertEqual(frappe.db.get_value("Quotation", made["name"], "party_name"), CUSTOMER_NAME)
		self.assertTrue(any("no price" in w for w in made["warnings"]))

	def test_the_customers_price_list_and_the_numbers_company_are_used(self):
		self.assertEqual(
			so._price_list(None), frappe.db.get_single_value("Selling Settings", "selling_price_list")
		)
		company = frappe.db.get_single_value("Global Defaults", "default_company")
		if not company or not frappe.db.has_column("WhatsApp Number", "company"):
			self.skipTest("No company field on this site")
		frappe.db.set_value("WhatsApp Number", A, "company", company)
		self.assertEqual(so._company(A), company)


# --- M12: a person's data ---------------------------------------------------------------------------


class TestPrivacy(PanelCase):
	def test_erase_removes_the_numbers_whatsapp_data_and_keeps_an_audit_row(self):
		chat_name = self._msg()
		with self.assertRaises(frappe.ValidationError):
			privacy.erase(CUSTOMER, "Asked on the phone", "0000")
		counts = privacy.erase(CUSTOMER, "Asked on the phone", CUSTOMER[-4:])
		self.assertFalse(frappe.db.exists("Periskope Chat", chat_name))
		self.assertFalse(frappe.db.exists("Periskope Message", {"chat_id": f"{CUSTOMER}@c.us"}))
		self.assertGreaterEqual(counts["Periskope Message"], 1)
		log = frappe.get_all(
			"Periskope Erasure Log", fields=["number_masked", "reason"], order_by="creation desc", limit=1
		)[0]
		self.assertEqual((log.number_masked, log.reason), (f"…{CUSTOMER[-4:]}", "Asked on the phone"))
		rep = _user("m12.rep@example.com", "Sales User")
		frappe.set_user(rep)
		with self.assertRaises(frappe.PermissionError):
			privacy.summary(CUSTOMER)


# --- M7: Meta self-healing --------------------------------------------------------------------------


class TestMeta(PanelCase):
	def test_a_late_update_copies_the_missing_message_first(self):
		from periskope_connector import meta

		doc = SimpleNamespace(name="WM-1", flags=frappe._dict(in_insert=False), status="read")
		with (
			patch.object(meta, "_ours", return_value=True),
			patch.object(meta, "ingest", return_value=None) as ingest,
		):
			meta.on_update(doc)
		ingest.assert_called_once_with("WM-1")
		with patch.object(meta, "account", return_value=None):
			self.assertEqual(meta.catch_up(), 0)


class TestStickerClassify(PanelCase):
	def test_a_sticker_never_goes_to_the_ai_even_when_saved_with_the_agent_off(self):
		# CI: with the integration off a message is saved unclassified, and classify() decides alone.
		from periskope_connector import messages
		from periskope_connector.ai import classifier
		from periskope_connector.tests.utils import event

		settings(enabled=0)
		name = messages.create_message(
			event(
				message_id="sticker_off_1", chat_id=f"{CUSTOMER}@c.us", message_type="sticker", body="x y z"
			)
		)
		with patch.object(classifier, "call_claude") as ai:
			classifier.classify(name)
		ai.assert_not_called()
		self.assertEqual(
			frappe.db.get_value("Periskope Message", name, ["message_type", "classification_error"]),
			("image", "Sticker"),
		)
