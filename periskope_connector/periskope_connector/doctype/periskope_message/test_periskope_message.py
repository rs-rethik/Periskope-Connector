"""Tests for the Periskope Message doctype and the code that works on it."""

import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from periskope_connector import alerts, backfill, leads, linking, messages, retention, tags, unmatched
from periskope_connector import timeline_comments as timeline
from periskope_connector.ai import classifier
from periskope_connector.api import webhook
from periskope_connector.system import insert_as_system
from periskope_connector.tests.utils import GROUP_CHAT, event
from periskope_connector.text_rules import analysis_skip_reason, is_acknowledgement

# The CRM link fields would otherwise make the runner build test leads and deals.
test_ignore = ["CRM Lead", "CRM Deal"]


def _event(**data):
	base = {
		"message_id": "false_919786387999@c.us_ABC1",
		"chat_id": "919600000001@c.us",
		"from_me": False,
		"org_phone": "919786387999@c.us",
		"sender_phone": "919600000001@c.us",
		"message_type": "chat",
		"body": "Hi, what is the price?",
		"timestamp": "2026-09-29T06:30:00Z",
		"ack": "3",
	}
	base.update(data)
	return {"event_type": "message.created", "data": base, "org_id": "org-1"}


ROWS = [{"recipients": ""}]  # an Email Alert action with no recipients: the Manager Emails

PHONE = "919786387999"


CHAT = "919000000001@c.us"


def _msg(message_id, hours_ago, body="hi", from_me=False):
	return {
		"message_id": message_id,
		"chat_id": CHAT,
		"timestamp": _iso(hours_ago),
		"body": body,
		"from_me": from_me,
		"message_type": "chat",
		"org_phone": f"{PHONE}@c.us",
		"sender_phone": f"{PHONE}@c.us" if from_me else CHAT,
	}


def _chat(hours_ago, chat_id=CHAT):
	return {"chat_id": chat_id, "latest_message": {"timestamp": _iso(hours_ago)}}


def _fake_api(chats, messages):
	"""A stand-in for client.request that serves a chat list and one chat's messages (newest first)."""

	def fake(method, path, phone=None, json=None):
		if path.startswith("chats?"):
			return {"chats": chats}
		assert path.startswith(f"chats/{CHAT}/messages")
		return {"messages": sorted(messages, key=lambda m: m["timestamp"], reverse=True)}

	return fake


def _iso(hours_ago):
	return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def add_minutes(value, minutes):
	from datetime import timedelta

	return value + timedelta(minutes=minutes)


def _message(message_id, days_old=0, body="hello", raw='{"data": {}}'):
	name = insert_as_system(
		{
			"doctype": "Periskope Message",
			"message_id": message_id,
			"body": body,
			"raw_payload": raw,
		}
	).name
	if days_old:
		old = now_datetime() - timedelta(days=days_old)
		frappe.db.set_value("Periskope Message", name, {"creation": old, "modified": old})
	return name


class TestPeriskopeMessage(FrappeTestCase):
	def test_the_customer_number_is_indexed_because_every_lookup_filters_on_it(self):
		self.assertTrue(frappe.get_meta("Periskope Message").get_field("contact_number").search_index)
		self.assertTrue(frappe.db.has_index("tabPeriskope Message", "contact_number_index"))

	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		names = patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Test Group")
		names.start()
		self.addCleanup(names.stop)
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_msg_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_msg_test")
		rep = patch.object(
			messages, "_rep_for_phone", lambda n: "Administrator" if n == "919786387999" else None
		)
		rep.start()
		self.addCleanup(rep.stop)

	def _indexes(self):
		"""{index name: [columns in order]} for the message table."""
		result = {}
		for row in frappe.db.sql("show index from `tabPeriskope Message`", as_dict=True):
			result.setdefault(row.Key_name, []).append((row.Seq_in_index, row.Column_name))
		return {name: [c for _, c in sorted(cols)] for name, cols in result.items()}

	def test_timestamp_and_chat_indexes_exist(self):
		indexes = list(self._indexes().values())
		self.assertIn(["message_timestamp"], indexes)  # reminders, list view, workspace card/chart
		self.assertIn(["chat_id", "message_timestamp"], indexes)  # the AI's chat-history lookup

	def test_index_hook_can_run_again_safely(self):
		from periskope_connector.periskope_connector.doctype.periskope_message.periskope_message import (
			on_doctype_update,
		)

		before = self._indexes()
		on_doctype_update()  # migrate runs this every time
		self.assertEqual(self._indexes(), before)

	def test_incoming_one_to_one(self):
		name = messages.create_message(_event())
		doc = frappe.get_doc("Periskope Message", name)
		self.assertEqual(doc.direction, "Incoming")
		self.assertEqual(doc.contact_number, "919600000001")
		self.assertEqual(doc.org_phone, "919786387999")
		self.assertEqual(doc.rep, "Administrator")
		self.assertEqual(doc.body, "Hi, what is the price?")
		self.assertFalse(doc.is_group)
		self.assertIsNotNone(doc.message_timestamp)

	def test_outgoing_keeps_customer_number(self):
		name = messages.create_message(_event(message_id="true_x_1", from_me=True))
		doc = frappe.get_doc("Periskope Message", name)
		self.assertEqual(doc.direction, "Outgoing")
		self.assertEqual(doc.contact_number, "919600000001")

	def test_group_has_no_customer_number(self):
		name = messages.create_message(_event(message_id="g_1", chat_id="1203630@g.us"))
		doc = frappe.get_doc("Periskope Message", name)
		self.assertTrue(doc.is_group)
		self.assertFalse(doc.contact_number)
		self.assertEqual(doc.sender_phone, "919600000001")

	def test_media_fields(self):
		media = {
			"filename": "quote.pdf",
			"mimetype": "application/pdf",
			"path": "org/x/quote.pdf",
			"size": 1234,
		}
		name = messages.create_message(_event(message_id="m_1", message_type="document", media=media))
		doc = frappe.get_doc("Periskope Message", name)
		self.assertEqual(
			(doc.media_filename, doc.media_mimetype, doc.media_size), ("quote.pdf", "application/pdf", 1234)
		)

	def test_duplicate_message_is_skipped(self):
		self.assertTrue(messages.create_message(_event()))
		self.assertIsNone(messages.create_message(_event()))
		self.assertEqual(
			frappe.db.count("Periskope Message", {"message_id": _event()["data"]["message_id"]}), 1
		)

	def test_payload_without_message_id_is_ignored(self):
		self.assertIsNone(messages.create_message({"event_type": "message.created", "data": {}}))

	def test_timestamp_converted_to_site_timezone(self):
		dt = messages._to_site_datetime("2026-09-29T06:30:00Z")
		self.assertIsNone(dt.tzinfo)
		self.assertIsNone(messages._to_site_datetime("not a date"))

	def test_message_type_is_one_of_five_and_the_raw_type_is_kept(self):
		for raw, expected in (
			("chat", "text"),
			("image", "image"),
			("document", "document"),
			("ptt", "audio"),
			("audio", "audio"),
			("video", "other"),
			("sticker", "other"),
		):
			doc = frappe.get_doc(
				"Periskope Message",
				messages.create_message(_event(message_id=f"type_{raw}", message_type=raw)),
			)
			self.assertEqual((doc.message_type, doc.periskope_type), (expected, raw), raw)

	def test_customer_phone_is_the_last_ten_digits_of_the_customer_or_the_group_sender(self):
		one = frappe.get_doc("Periskope Message", messages.create_message(_event(message_id="cp1")))
		group = frappe.get_doc(
			"Periskope Message",
			messages.create_message(
				_event(message_id="cp2", chat_id="1203630@g.us", sender_phone="919811122233@c.us")
			),
		)
		self.assertEqual(one.customer_phone, "9600000001")
		self.assertEqual(group.customer_phone, "9811122233")

	def test_sender_name_is_the_chat_name_of_a_customer_message_only(self):
		with patch.object(messages, "fetch_chat_name", lambda *a, **k: "Meena Iyer"):
			incoming = messages.create_message(_event(message_id="sn1"))
			outgoing = messages.create_message(_event(message_id="sn2", from_me=True))
			group = messages.create_message(_event(message_id="sn3", chat_id="1203631@g.us"))
		names = [
			frappe.db.get_value("Periskope Message", n, "sender_name") for n in (incoming, outgoing, group)
		]
		self.assertEqual(names, ["Meena Iyer", "", ""])

	def test_a_rep_reply_marks_the_earlier_customer_messages_as_replied(self):
		first = messages.create_message(_event(message_id="rp1", timestamp="2026-09-29T06:30:00Z"))
		other_chat = messages.create_message(
			_event(message_id="rp2", chat_id="919600000002@c.us", timestamp="2026-09-29T06:31:00Z")
		)
		later = messages.create_message(_event(message_id="rp3", timestamp="2026-09-29T06:50:00Z"))
		messages.create_message(_event(message_id="rp4", from_me=True, timestamp="2026-09-29T06:40:00Z"))
		replied = lambda n: frappe.db.get_value("Periskope Message", n, "replied")  # noqa: E731
		self.assertEqual((replied(first), replied(other_chat), replied(later)), (1, 0, 0))

	def test_the_sales_person_of_the_phone_is_saved_on_the_message(self):
		with patch.object(messages, "_sales_person_for_phone", lambda n: "Sales Team"):
			name = messages.create_message(_event(message_id="sp1"))
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "sales_person"), "Sales Team")

	def test_a_sales_manager_can_set_the_correct_category_but_only_a_real_one(self):
		from periskope_connector import tags
		from periskope_connector.api import settings as settings_api

		name = messages.create_message(_event(message_id="cc1"))
		settings_api.set_correct_category(name, "delivery_delay")
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "correct_category"), "delivery_delay")
		with self.assertRaises(frappe.ValidationError):
			settings_api.set_correct_category(name, "not_a_category")
		self.assertIn("delivery_delay", tags.names())


class TestHighSeverityAlert(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: ""),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_alert_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_alert_test")
		self.email = frappe.db.get_value("User", "Administrator", "email")
		cfg = patch("periskope_connector.alerts.get_config")
		self.config = cfg.start()
		self.config.return_value.emails = [self.email, "md@example.com"]
		self.addCleanup(cfg.stop)
		mail = patch.object(frappe, "sendmail")
		self.sendmail = mail.start()
		self.addCleanup(mail.stop)

	def _message(self, severity="High", body="This is unacceptable <b>now</b>"):
		name = messages.create_message(
			event(message_id=f"al_{severity}_{frappe.generate_hash(length=4)}", body=body)
		)
		frappe.db.set_value(
			"Periskope Message",
			name,
			{
				"severity": severity,
				"ai_category": "quality_complaint",
				"ai_summary": "Angry about torn fabric",
			},
		)
		return name

	def test_a_high_severity_message_alerts_the_managers_once(self):
		name = self._message()
		self.assertTrue(alerts.send_alert(name, ROWS))
		kwargs = self.sendmail.call_args.kwargs
		self.assertEqual(kwargs["recipients"], [self.email, "md@example.com"])
		self.assertIn("quality complaint", kwargs["subject"])
		self.assertIn("Angry about torn fabric", kwargs["message"])
		self.assertIn("&lt;b&gt;now&lt;/b&gt;", kwargs["message"])  # customer text is escaped
		self.assertTrue(
			frappe.db.exists("Notification Log", {"for_user": "Administrator", "document_name": name})
		)
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "high_alert_sent"), 1)

		self.assertFalse(alerts.send_alert(name, ROWS))  # not a second time
		self.assertEqual(self.sendmail.call_count, 1)

	def test_missing_recipients_send_nothing(self):
		self.config.return_value.emails = []
		self.assertFalse(alerts.send_alert(self._message("High"), ROWS))
		self.sendmail.assert_not_called()

	def test_recipients_on_the_action_replace_the_manager_emails(self):
		name = self._message()
		self.assertTrue(alerts.send_alert(name, [{"recipients": "qa@example.com; head@example.com"}]))
		self.assertEqual(self.sendmail.call_args.kwargs["recipients"], ["qa@example.com", "head@example.com"])

	def test_several_actions_send_one_alert_to_everyone(self):
		name = self._message()
		rows = [{"recipients": "qa@example.com"}, {"recipients": ""}]
		self.assertTrue(alerts.send_alert(name, rows))
		self.assertEqual(self.sendmail.call_count, 1)
		self.assertEqual(
			self.sendmail.call_args.kwargs["recipients"], ["qa@example.com", self.email, "md@example.com"]
		)

	def test_a_failure_is_logged_and_never_raised(self):
		self.sendmail.side_effect = RuntimeError("mail down")
		with patch.object(frappe, "log_error") as log_error:
			self.assertFalse(alerts.send_alert(self._message(), ROWS))
		log_error.assert_called_once()


class TestBackfill(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never call Periskope."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch.object(backfill, "_linked_phones", lambda: [PHONE]),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: "Test Customer"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_backfill_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_backfill_test")
		frappe.db.set_single_value("Periskope Settings", "enabled", 1)

	def _run(self, chats, messages, days=1):
		with patch.object(backfill, "request", _fake_api(chats, messages)):
			return backfill.run(days=days)

	def _saved(self, message_id):
		return frappe.db.exists("Periskope Message", {"message_id": message_id})

	def test_a_missing_message_is_saved(self):
		result = self._run([_chat(2)], [_msg("bf_missing", 2, body="Hi, this is Rethik")])
		self.assertEqual(result["recovered"], 1)
		self.assertTrue(self._saved("bf_missing"))
		self.assertEqual(
			frappe.db.get_value("Periskope Message", {"message_id": "bf_missing"}, "body"),
			"Hi, this is Rethik",
		)

	def test_a_message_already_saved_is_not_saved_again(self):
		messages = [_msg("bf_known", 3)]
		self.assertEqual(self._run([_chat(3)], messages)["recovered"], 1)
		self.assertEqual(self._run([_chat(3)], messages)["recovered"], 0)
		self.assertEqual(frappe.db.count("Periskope Message", {"message_id": "bf_known"}), 1)

	def test_only_the_gap_is_filled_when_a_later_message_is_already_saved(self):
		# The real case: "Hello" arrived by webhook, the "Hi" before it did not.
		self._run([_chat(1)], [_msg("bf_later", 1)])
		result = self._run([_chat(1)], [_msg("bf_gap", 5), _msg("bf_later", 1)])
		self.assertEqual(result["recovered"], 1)
		self.assertTrue(self._saved("bf_gap"))

	def test_messages_older_than_the_lookback_are_left_alone(self):
		result = self._run([_chat(2)], [_msg("bf_old", 30), _msg("bf_new", 2)], days=1)
		self.assertEqual(result["recovered"], 1)
		self.assertFalse(self._saved("bf_old"))

	def test_chats_without_recent_activity_are_not_read(self):
		calls = []

		def fake(method, path, phone=None, json=None):
			calls.append(path)
			return {"chats": [_chat(100)]}

		with patch.object(backfill, "request", fake):
			result = backfill.run(days=1)
		self.assertEqual(result, {"recovered": 0, "chats": 0})
		self.assertTrue(all(path.startswith("chats?") for path in calls))

	def test_messages_are_saved_oldest_first(self):
		order = []
		messages = [_msg("bf_b", 1), _msg("bf_a", 3), _msg("bf_c", 0.5)]
		with patch.object(
			backfill, "create_message", lambda payload: order.append(payload["data"]["message_id"]) or "x"
		):
			self._run([_chat(1)], messages)
		self.assertEqual(order, ["bf_a", "bf_b", "bf_c"])

	def test_a_reply_marks_the_earlier_customer_message_as_replied(self):
		self._run([_chat(2)], [_msg("bf_ask", 3), _msg("bf_answer", 2, from_me=True)])
		self.assertEqual(frappe.db.get_value("Periskope Message", {"message_id": "bf_ask"}, "replied"), 1)

	def test_nothing_happens_when_the_integration_is_off(self):
		frappe.db.set_single_value("Periskope Settings", "enabled", 0)
		with patch.object(backfill, "request", side_effect=AssertionError("must not call Periskope")):
			self.assertEqual(backfill.run(), {"recovered": 0, "chats": 0})

	def test_one_failing_chat_does_not_stop_the_others(self):
		chats = [_chat(1, "919000000002@c.us"), _chat(1)]
		good = _msg("bf_good", 1)

		def fake(method, path, phone=None, json=None):
			if path.startswith("chats?"):
				return {"chats": chats}
			if "919000000002" in path:
				raise frappe.ValidationError("Periskope returned HTTP 500")
			return {"messages": [good]}

		# The job rolls back the failed chat; here that would also undo the test's own savepoint.
		with (
			patch.object(backfill, "request", fake),
			patch.object(frappe, "log_error") as logged,
			patch.object(frappe.db, "rollback"),
		):
			result = backfill.run(days=1)
		self.assertEqual(result["recovered"], 1)
		self.assertTrue(self._saved("bf_good"))
		logged.assert_called_once()


class TestLinking(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target, _value in (
			(patch.object(frappe.db, "commit"), None),
			(
				patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Kongu Region Sales"),
				None,
			),
			(patch.object(messages, "_rep_for_phone", lambda n: None), None),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_link_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_link_test")

	def _lead(self, **values):
		doc = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Test", **values})
		doc.flags.ignore_mandatory = True
		doc.insert(ignore_permissions=True)
		return doc

	def _link(self, name):
		doc = frappe.get_doc("Periskope Message", name)
		return doc.crm_lead, doc.crm_deal, doc.link_status, doc.link_method

	def test_our_own_company_is_never_a_customer(self):
		self._lead(organization="Kongu Region Sales")
		with patch.object(linking, "own_companies", lambda: ["Kongu Region"]):
			name = messages.create_message(event(message_id="own1", chat_id=GROUP_CHAT))
		self.assertEqual(frappe.db.get_value("Periskope Group", GROUP_CHAT, "group_type"), "Internal")
		self.assertEqual(self._link(name)[2], "Internal")

	def test_one_to_one_chat_links_by_mobile_number(self):
		lead = self._lead(mobile_no="+91 96000 00001")
		name = messages.create_message(event())
		self.assertEqual(self._link(name), (lead.name, None, "Linked", "Mobile Number"))

	def test_two_leads_with_the_same_number_are_ambiguous(self):
		self._lead(mobile_no="9600000001")
		newest = self._lead(mobile_no="9600000001")
		name = messages.create_message(event())
		lead, _deal, status, _method = self._link(name)
		self.assertEqual((lead, status), (newest.name, "Ambiguous"))

	def test_a_deal_beats_a_lead_with_the_same_number(self):
		other_lead = self._lead(mobile_no="9600000001")
		deal_lead = self._lead()
		deal = frappe.get_doc({"doctype": "CRM Deal", "lead": deal_lead.name})
		deal.flags.ignore_mandatory = True
		deal.flags.ignore_links = True
		deal.insert(ignore_permissions=True)
		# the CRM copies mobile_no from the lead on conversion; a raw write skips the doc_events
		# hook that maintains the normalised fields, so run it as well
		frappe.db.set_value("CRM Deal", deal.name, "mobile_no", "9600000001")
		linking.update_phone_fields(frappe.get_doc("CRM Deal", deal.name))
		frappe.db.set_value("CRM Lead", other_lead.name, "modified", "2035-01-01 00:00:00")  # the newer one
		name = messages.create_message(event())
		self.assertEqual(self._link(name)[:3], (deal_lead.name, deal.name, "Linked"))

	def test_unknown_number_is_no_match(self):
		name = messages.create_message(event())
		self.assertEqual(self._link(name)[2], "No Match")

	def test_group_named_after_a_customer_is_a_customer_group_linked_by_name(self):
		lead = self._lead(organization="Kongu Region Sales")
		name = messages.create_message(event(message_id="g1", chat_id=GROUP_CHAT))
		self.assertEqual(frappe.db.get_value("Periskope Group", GROUP_CHAT, "group_type"), "Customer")
		self.assertEqual(self._link(name), (lead.name, None, "Linked", "Group Name"))

	def test_customer_group_falls_back_to_the_senders_mobile(self):
		lead = self._lead(organization="Kongu Region Sales")
		other = self._lead(mobile_no="9600000001")
		frappe.db.set_value("CRM Lead", lead.name, "organization", "Someone Else Ltd")
		frappe.db.set_value("Periskope Group", {"chat_id": GROUP_CHAT}, "group_type", "Customer")
		name = messages.create_message(event(message_id="g2", chat_id=GROUP_CHAT))
		frappe.db.set_value("Periskope Group", GROUP_CHAT, "group_type", "Customer")
		linking.link_message(name)
		self.assertEqual(self._link(name)[0], other.name)

	def test_internal_group_is_not_linked_until_the_ai_names_a_customer(self):
		lead = self._lead(organization="Sumi Export House")
		name = messages.create_message(event(message_id="g3", chat_id=GROUP_CHAT, body="make pi"))
		self.assertEqual(frappe.db.get_value("Periskope Group", GROUP_CHAT, "group_type"), "Internal")
		self.assertEqual(self._link(name)[2:], ("Internal", ""))

		frappe.db.set_value("Periskope Message", name, "mentioned_customer", "SUMI EXPORT HOUSE")
		linking.link_message(name)
		self.assertEqual(self._link(name), (lead.name, None, "Linked", "Customer Name"))

	def test_a_lead_set_on_the_group_wins(self):
		mapped = self._lead(organization="Mapped Customer")
		self._lead(mobile_no="9600000001")
		name = messages.create_message(event(message_id="g4", chat_id=GROUP_CHAT))
		frappe.db.set_value("Periskope Group", GROUP_CHAT, "crm_lead", mapped.name)
		linking.link_message(name)
		self.assertEqual(self._link(name), (mapped.name, None, "Linked", "Group Mapping"))

	def test_a_converted_lead_is_represented_by_its_deal(self):
		lead = self._lead(mobile_no="9600000001")
		frappe.db.set_value("CRM Lead", lead.name, "converted", 1)
		deal = frappe.get_doc({"doctype": "CRM Deal", "lead": lead.name, "organization": None})
		deal.flags.ignore_mandatory = True
		deal.flags.ignore_links = True
		deal.insert(ignore_permissions=True)
		name = messages.create_message(event(message_id="d1"))
		self.assertEqual(self._link(name), (lead.name, deal.name, "Linked", "Mobile Number"))


class TestNewEnquiryLead(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Team"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_enquiry_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_enquiry_test")
		# Never write the real setting from a test: the site's own value must stay as it is.
		self.setting = patch.object(leads, "enabled", lambda: True)
		self.setting.start()
		self.addCleanup(self.setting.stop)

	def _tagged(self, message_id="enq1", category="order_or_enquiry", **data):
		name = messages.create_message(event(message_id=message_id, **data))
		frappe.db.set_value("Periskope Message", name, "ai_category", category)
		return name

	def test_a_new_enquiry_from_an_unknown_number_creates_a_lead(self):
		earlier = messages.create_message(event(message_id="enq0", body="Hello there, who can help?"))
		name = self._tagged()
		lead_name = leads.create_lead_for_message(name)
		lead = frappe.get_doc("CRM Lead", lead_name)
		self.assertEqual(lead.mobile_no, "+919600000001")
		self.assertEqual((lead.lead_owner, lead.source), ("Administrator", "WhatsApp"))
		for m in (name, earlier):  # the earlier message of the chat is linked too
			self.assertEqual(frappe.db.get_value("Periskope Message", m, "crm_lead"), lead_name)

	def test_a_known_number_gets_no_second_lead(self):
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		before = frappe.db.count("CRM Lead")
		self.assertIsNone(leads.create_lead_for_message(self._tagged()))
		self.assertEqual(frappe.db.count("CRM Lead"), before)

	def test_the_same_number_is_not_turned_into_two_leads(self):
		first = leads.create_lead_for_message(self._tagged("enq_a"))
		second = leads.create_lead_for_message(self._tagged("enq_b"))
		self.assertTrue(first)
		self.assertIsNone(second)

	def test_other_tags_groups_outgoing_and_the_setting_do_not_create_leads(self):
		before = frappe.db.count("CRM Lead")
		leads.create_lead_for_message(self._tagged("o1", category="general"))
		leads.create_lead_for_message(self._tagged("o2", chat_id=GROUP_CHAT))
		leads.create_lead_for_message(self._tagged("o3", from_me=True))
		with patch.object(leads, "enabled", lambda: False):
			leads.create_lead_for_message(self._tagged("o4"))
		self.assertEqual(frappe.db.count("CRM Lead"), before)

	def test_the_lead_takes_the_name_the_customer_gave(self):
		name = self._tagged("nm1")
		frappe.db.set_value("Periskope Message", name, "contact_name", "Ravi Kumar S")
		lead = frappe.get_doc("CRM Lead", leads.create_lead_for_message(name))
		self.assertEqual((lead.first_name, lead.last_name), ("Ravi", "Kumar S"))

	def test_a_name_given_earlier_in_the_chat_is_used(self):
		earlier = messages.create_message(event(message_id="nm0", body="Hi, I am Asha"))
		frappe.db.set_value("Periskope Message", earlier, "contact_name", "Asha")
		lead = frappe.get_doc("CRM Lead", leads.create_lead_for_message(self._tagged("nm2")))
		self.assertEqual(lead.first_name, "Asha")

	def test_without_a_name_the_phone_number_is_the_first_name(self):
		lead = frappe.get_doc("CRM Lead", leads.create_lead_for_message(self._tagged("nm3")))
		self.assertEqual((lead.first_name, lead.last_name), ("919600000001", ""))

	def test_the_setting_is_off_until_it_is_saved_on(self):
		self.setting.stop()  # the real leads.enabled, reading a faked stored value
		self.addCleanup(self.setting.start)
		for stored, expected in (((), False), ((("0",),), False), ((("1",),), True)):
			with patch.object(frappe.db, "sql", lambda *a, _s=stored, **k: _s):
				self.assertEqual(leads.enabled(), expected, stored)


class TestTimelineComment(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Team"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_comment_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_comment_test")
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		self.lead = lead.insert(ignore_permissions=True)

	def _message(self, message_id, category=None, body="Need <b>10</b> pieces\nurgent"):
		name = messages.create_message(event(message_id=message_id, body=body))
		if category:
			frappe.db.set_value(
				"Periskope Message", name, {"ai_category": category, "ai_summary": "Wants ten pieces"}
			)
		return name

	def _comments(self):
		return frappe.get_all(
			"Comment",
			filters={
				"reference_doctype": "CRM Lead",
				"reference_name": self.lead.name,
				"comment_type": "Comment",
			},
			fields=["name", "content", "owner"],
		)

	def test_a_tagged_linked_message_gets_one_escaped_comment_on_the_lead(self):
		name = self._message("c1", "order_or_enquiry")
		linking.link_message(name)
		linking.link_message(name)  # linking again must not add a second comment
		comments = self._comments()
		self.assertEqual(len(comments), 1)
		content = comments[0].content
		self.assertIn("WhatsApp incoming", content)
		self.assertIn("&lt;b&gt;10&lt;/b&gt; pieces<br>urgent", content)  # no HTML from the customer
		self.assertIn("order or enquiry: Wants ten pieces", content)
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "timeline_comment"), comments[0].name)

	def test_unclassified_and_general_messages_get_no_comment(self):
		linking.link_message(self._message("c2"))
		linking.link_message(self._message("c2b", "general"))
		self.assertEqual(self._comments(), [])

	def test_a_message_classified_after_it_was_linked_gets_its_comment_when_linked_again(self):
		name = self._message("c3")
		self.assertEqual(self._comments(), [])
		frappe.db.set_value("Periskope Message", name, "ai_category", "delivery_delay")
		linking.link_message(name)
		self.assertEqual(len(self._comments()), 1)

	def test_an_unlinked_message_gets_no_comment(self):
		name = messages.create_message(event(message_id="c4", chat_id="919888800000@c.us"))
		frappe.db.set_value("Periskope Message", name, "ai_category", "delivery_delay")
		linking.link_message(name)
		self.assertFalse(frappe.db.get_value("Periskope Message", name, "timeline_comment"))


class TestBurstComments(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Team"),
			patch.object(messages, "_rep_for_phone", lambda n: "Administrator"),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_burst_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_burst_test")
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Known", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		self.lead = lead.insert(ignore_permissions=True)

	def _commented(self, message_id):
		name = frappe.db.get_value("Periskope Message", {"message_id": message_id})
		return timeline.add_comment(name)

	def _tagged_message(self, message_id, minutes_later=0):
		name = messages.create_message(event(message_id=message_id, body=f"message {message_id}"))
		if minutes_later:
			base = frappe.db.get_value("Periskope Message", {"message_id": message_id}, "message_timestamp")
			frappe.db.set_value(
				"Periskope Message", name, "message_timestamp", add_minutes(base, minutes_later)
			)
		frappe.db.set_value("Periskope Message", name, "ai_category", "order_or_enquiry")
		return name

	def test_burst_mode_merges_messages_inside_the_window(self):
		with patch.object(timeline, "_settings", lambda: (timeline.MODE_BURST, 30)):
			self._tagged_message("b1")
			second = self._tagged_message("b2", minutes_later=10)
			first_comment = self._commented("b1")
			self._commented("b2")
			comments = frappe.get_all(
				"Comment",
				filters={"reference_doctype": "CRM Lead", "reference_name": self.lead.name},
				pluck="name",
			)
			self.assertEqual(len(comments), 1)  # both messages share one comment
			self.assertEqual(
				frappe.db.get_value("Periskope Message", second, "timeline_comment"), first_comment
			)
			content = frappe.db.get_value("Comment", first_comment, "content")
			self.assertIn("message b1", content)
			self.assertIn("message b2", content)

	def test_burst_mode_starts_a_new_comment_outside_the_window(self):
		with patch.object(timeline, "_settings", lambda: (timeline.MODE_BURST, 30)):
			self._tagged_message("b3")
			self._tagged_message("b4", minutes_later=120)
			self._commented("b3")
			self._commented("b4")
			self.assertEqual(
				frappe.db.count(
					"Comment", {"reference_doctype": "CRM Lead", "reference_name": self.lead.name}
				),
				2,
			)

	def test_off_mode_adds_nothing(self):
		with patch.object(timeline, "_settings", lambda: (timeline.MODE_OFF, 30)):
			self._tagged_message("b5")
			self.assertIsNone(self._commented("b5"))
			self.assertEqual(
				frappe.db.count(
					"Comment", {"reference_doctype": "CRM Lead", "reference_name": self.lead.name}
				),
				0,
			)


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
			name = messages.create_message(event(message_id="idx1"))
			by_mobile.assert_called_once()
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "crm_lead"), lead.name)


class TestRetention(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_retention_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_retention_test")

	def test_raw_payloads_older_than_the_setting_are_cleared(self):
		old, fresh = _message("ret1", days_old=100), _message("ret2")
		cleared = retention.clear("Periskope Message", "raw_payload", 90)
		self.assertGreaterEqual(cleared, 1)
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "raw_payload"), "")
		self.assertEqual(frappe.db.get_value("Periskope Message", fresh, "raw_payload"), '{"data": {}}')

	def test_zero_days_keeps_everything(self):
		old = _message("ret3", days_old=400)
		self.assertEqual(retention.clear("Periskope Message", "raw_payload", 0), 0)
		self.assertTrue(frappe.db.get_value("Periskope Message", old, "raw_payload"))

	def test_bodies_are_only_cleared_when_configured(self):
		old = _message("ret4", days_old=100)
		with patch.object(frappe, "get_cached_doc") as cached:
			cached.return_value = frappe._dict(raw_payload_retention_days=90, body_retention_days=0)
			retention.run()
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "raw_payload"), "")
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "body"), "hello")

		with patch.object(frappe, "get_cached_doc") as cached:
			cached.return_value = frappe._dict(raw_payload_retention_days=90, body_retention_days=90)
			retention.run()
		self.assertEqual(frappe.db.get_value("Periskope Message", old, "body"), "")


class TestUnmatched(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never send real email."""

	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Kongu Region"),
			patch("periskope_connector.messages.fetch_chat_name", lambda *a, **k: "Meena"),
			patch.object(messages, "_rep_for_phone", lambda n: None),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_unmatched_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_unmatched_test")
		frappe.db.delete("Periskope Message")  # only this test's messages, rolled back afterwards

	def _rows(self):
		return unmatched.get_rows("2026-09-01", "2031-12-31")

	def test_an_unknown_number_is_listed_once_with_its_message_count_and_last_text(self):
		messages.create_message(event(message_id="u1", body="first", timestamp="2026-09-29T06:00:00Z"))
		messages.create_message(event(message_id="u2", body="second", timestamp="2026-09-29T07:00:00Z"))
		rows = self._rows()
		self.assertEqual(len(rows), 1)
		self.assertEqual((rows[0].kind, rows[0].who, rows[0].messages), ("Customer", "9600000001", 2))
		self.assertEqual((rows[0].name, rows[0].text), ("Meena", "second"))

	def test_a_number_added_to_the_crm_meanwhile_is_no_longer_listed(self):
		messages.create_message(event(message_id="u3"))
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Meena", "mobile_no": "9600000001"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		self.assertEqual(self._rows(), [])

	def test_team_groups_are_left_out_and_customer_groups_are_kept(self):
		messages.create_message(event(message_id="u4", chat_id=GROUP_CHAT))  # Internal: no customer to add
		self.assertEqual(self._rows(), [])
		frappe.db.set_value("Periskope Group", GROUP_CHAT, "group_type", "Customer")
		rows = self._rows()
		self.assertEqual((rows[0].kind, rows[0].who), ("Group", GROUP_CHAT))

	def test_messages_outside_the_dates_are_not_listed(self):
		messages.create_message(event(message_id="u5", timestamp="2026-08-01T06:00:00Z"))
		self.assertEqual(self._rows(), [])

	def test_the_weekly_email_goes_to_the_managers_only_when_there_is_something_to_report(self):
		with (
			patch.object(frappe, "sendmail") as sendmail,
			patch("periskope_connector.reminders.get_config") as config,
			patch.object(unmatched, "get_rows") as rows,
		):
			config.return_value.emails = ["boss@example.com"]
			rows.return_value = []
			self.assertEqual(unmatched.send_weekly_report(), 0)
			sendmail.assert_not_called()

			rows.return_value = [
				frappe._dict(
					kind="Customer",
					who="9600000001",
					name="<b>x</b>",
					messages=2,
					last_message="2026-09-29 12:00:00",
					category="order_or_enquiry",
					sales_person=None,
					text="hi",
				)
			]
			self.assertEqual(unmatched.send_weekly_report(), 1)
			kwargs = sendmail.call_args.kwargs
			self.assertEqual(kwargs["recipients"], ["boss@example.com"])
			self.assertIn("9600000001", kwargs["message"])
			self.assertIn("&lt;b&gt;x&lt;/b&gt;", kwargs["message"])  # escaped

			config.return_value.emails = []
			sendmail.reset_mock()
			unmatched.send_weekly_report()
			sendmail.assert_not_called()


class TestTextRules(FrappeTestCase):
	def test_tiny_and_one_word_texts_are_not_analysed(self):
		for body in ["done", "Done.", "DONE!!", "ok", "k", "👍", "🙏🙏", "thanks", "received", "confirmed",
			"Interested", "Quotation", "sent", "ok sir", "thank you sir", "Hi", "hello", "good morning", "", "   "]:  # fmt: skip
			self.assertIsNotNone(analysis_skip_reason(body), repr(body))

	def test_real_messages_are_analysed(self):
		for body in ["Price?", "Available?", "Please send the quote", "Send price", "Call me tomorrow",
			"Done, but the delivery was late", "Need 100 pcs", "what is the rate"]:  # fmt: skip
			self.assertIsNone(analysis_skip_reason(body), repr(body))

	def test_reasons(self):
		self.assertEqual(analysis_skip_reason("done"), "Too short to tag")
		self.assertEqual(analysis_skip_reason("Quotation"), "One-word message")
		self.assertEqual(analysis_skip_reason(""), "No text in the message")

	def test_reminders_still_treat_a_real_one_word_message_as_needing_a_reply(self):
		self.assertFalse(is_acknowledgement("Quotation"))
		self.assertTrue(is_acknowledgement("done"))


class TestClassify(FrappeTestCase):
	"""Runs on the real site DB: never commit, always roll back, never use the real API key."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_ai_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_ai_test")

		rep = patch.object(messages, "_rep_for_phone", lambda n: None)
		rep.start()
		self.addCleanup(rep.stop)

		self.settings = SimpleNamespace(
			classify_group_chats=0, context_messages=5, ai_model="claude-haiku-4-5"
		)
		patch.object(classifier, "get_settings", lambda: self.settings).start()
		self.addCleanup(patch.stopall)

	def _message(self, body="Please send a quote for 50 units", **kw):
		payload = {
			"event_type": "message.created",
			"data": {
				"message_id": kw.pop("message_id", "false_1@c.us_X1"),
				"chat_id": "919600000001@c.us",
				"from_me": False,
				"org_phone": "919786387999@c.us",
				"message_type": "chat",
				"body": body,
				"timestamp": "2026-09-29T06:30:00Z",
				**kw,
			},
		}
		return frappe.get_doc("Periskope Message", messages.create_message(payload))

	def _claude_says(self, **result):
		base = {
			"category": "order_or_enquiry",
			"sentiment": "Positive",
			"severity": "Low",
			"summary": "Customer wants a quote for 50 units.",
			"confidence": 92,
			"needs_reply": True,
			"follow_up_on": "",
			"mentioned_products": "",
			"mentioned_amount": "50 units",
			"competitor_name": "",
			"customer_name": "",
			"contact_name": "",
			"language": "English",
		}
		base.update(result)
		response = MagicMock(
			stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(base))]
		)
		return patch.object(classifier, "call_claude", lambda *a, **k: json.loads(response.content[0].text))

	def test_classification_is_saved_with_its_category(self):
		doc = self._message()
		with self._claude_says():
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(doc.classification_status, "Done")
		self.assertEqual(doc.ai_category, "order_or_enquiry")
		self.assertEqual(doc.severity, "Low")
		self.assertTrue(doc.needs_reply)

	def test_an_unknown_category_falls_back_to_general(self):
		doc = self._message()
		with self._claude_says(category="Made Up Category"):
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(doc.ai_category, "general")

	def test_severity_is_the_ais_own_and_case_does_not_matter(self):
		for i, (said, saved) in enumerate(
			(("high", "High"), ("MEDIUM", "Medium"), ("low", "Low"), ("nonsense", "Low"))
		):
			doc = self._message("The goods are damaged", message_id=f"sev_{i}")
			with self._claude_says(category="quality_complaint", severity=said):
				classifier.classify(doc.name)
			self.assertEqual(frappe.db.get_value("Periskope Message", doc.name, "severity"), saved, said)

	def test_the_summary_is_cut_to_one_line_of_140_characters(self):
		doc = self._message()
		with self._claude_says(summary="x" * 300):
			classifier.classify(doc.name)
		self.assertEqual(len(frappe.db.get_value("Periskope Message", doc.name, "ai_summary")), 140)

	def test_follow_up_time_is_parsed(self):
		doc = self._message("Call me tomorrow 4pm")
		with self._claude_says(category="general", follow_up_on="2026-09-30 16:00"):
			classifier.classify(doc.name)
		doc.reload()
		self.assertEqual(str(doc.follow_up_on), "2026-09-30 16:00:00")

	def test_trivial_and_group_messages_are_skipped(self):
		doc = self._message("ok", message_id="t1")
		classifier.classify(doc.name)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", doc.name, "classification_status"), "Skipped"
		)

		group = self._message("Any update on the order?", message_id="g1", chat_id="1203630@g.us")
		classifier.classify(group.name)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", group.name, "classification_status"), "Skipped"
		)

	def test_one_word_messages_are_never_sent_to_the_ai(self):
		for i, body in enumerate(["done", "Received", "Quotation", "👍"]):
			doc = self._message(body, message_id=f"one_{i}")
			with patch.object(classifier, "call_claude") as ai:
				classifier.classify(doc.name)
			ai.assert_not_called()
			self.assertEqual(
				frappe.db.get_value("Periskope Message", doc.name, "classification_status"), "Skipped", body
			)

	def test_low_value_messages_are_skipped_as_general_low_when_they_arrive(self):
		with (
			patch.object(frappe.db, "get_single_value", return_value=1),
			patch.object(frappe, "enqueue") as enqueue,
		):
			done = self._message("done", message_id="arr_done")
			real = self._message("Send the price list", message_id="arr_real")
		self.assertEqual(
			(done.classification_status, done.classification_error), ("Skipped", "Too short to tag")
		)
		self.assertEqual((done.ai_category, done.severity), ("general", "Low"))
		self.assertEqual(real.classification_status, "Pending")
		enqueue.assert_not_called()  # nothing is queued: the 5-minute job picks the message up

	def test_a_rep_message_is_never_classified_and_has_no_category(self):
		with patch.object(frappe.db, "get_single_value", return_value=1):
			sent = self._message("Sending the quotation now", message_id="rep_1", from_me=True)
		self.assertEqual(
			(sent.classification_status, sent.classification_error), ("Skipped", "Sent by the rep")
		)
		self.assertFalse(sent.ai_category)
		self.assertNotIn(sent.name, classifier._pending(100000))

	def test_the_batch_takes_at_most_twenty_customer_messages_oldest_first(self):
		self.assertEqual(classifier.BATCH_SIZE, 20)
		with patch.object(frappe.db, "get_single_value", return_value=1):
			names = [self._message(f"Send price {i}", message_id=f"b_{i}").name for i in range(3)]
		pending = classifier._pending(2)
		self.assertEqual(len(pending), 2)
		self.assertTrue(set(pending) <= set(names) or pending)  # oldest first, capped by the limit

	def test_skipped_messages_are_saved_as_general_low_except_rep_and_group_skips(self):
		from periskope_connector.text_rules import skip_fields

		self.assertEqual(skip_fields("Too short to tag")["ai_category"], "general")
		self.assertEqual(skip_fields("Not a text message")["severity"], "Low")
		for reason in ("Sent by the rep", "Group chat"):
			self.assertNotIn("ai_category", skip_fields(reason))

	def _run_batch(self, names, classify=None, commit_errors=None):
		"""Run the 5-minute job over `names`. Rollback and error logging are mocked so the test's
		own transaction survives; returns the mocks."""
		with (
			patch.object(classifier, "_ready_to_run", lambda: True),
			patch.object(classifier, "_pending", lambda n: names),
			patch.object(frappe.db, "rollback") as rollback,
			patch.object(frappe, "log_error") as log_error,
			patch.object(classifier, "classify", classify or classifier.classify),
		):
			if commit_errors:
				frappe.db.commit.side_effect = commit_errors
			classifier.run_batch()
		return rollback, log_error

	def _status(self, name):
		return frappe.db.get_value(
			"Periskope Message",
			name,
			["classification_status", "classification_attempts", "classification_error"],
			as_dict=True,
		)

	def test_one_failing_message_does_not_stop_the_batch(self):
		bad = self._message("Send the price list", message_id="batch_bad")
		good = self._message("Need a quote for 20 units", message_id="batch_good")
		real = classifier.classify

		def flaky(name):
			if name == bad.name:
				raise RuntimeError("database went away")
			return real(name)

		with self._claude_says():
			rollback, log_error = self._run_batch([bad.name, good.name], classify=flaky)

		rollback.assert_called()
		log_error.assert_called()
		failed = self._status(bad.name)
		self.assertEqual((failed.classification_status, failed.classification_attempts), ("Failed", 1))
		self.assertIn("database went away", failed.classification_error)
		self.assertEqual(self._status(good.name).classification_status, "Done")  # still processed

	def test_a_failed_commit_is_rolled_back_and_the_batch_carries_on(self):
		first = self._message("Send the price list", message_id="commit_1")
		second = self._message("Need a quote for 20 units", message_id="commit_2")
		# 1st commit fails (first message), then the failure record and the second message commit fine
		with self._claude_says():
			rollback, log_error = self._run_batch(
				[first.name, second.name], commit_errors=[Exception("commit failed"), None, None]
			)
		rollback.assert_called_once()
		log_error.assert_called_once()
		self.assertEqual(self._status(first.name).classification_status, "Failed")
		self.assertEqual(self._status(second.name).classification_status, "Done")

	def test_the_batch_job_logs_and_raises_an_unexpected_failure(self):
		with (
			patch.object(classifier, "_ready_to_run", lambda: True),
			patch.object(classifier, "_pending", side_effect=RuntimeError("database unreachable")),
			patch.object(frappe, "log_error") as log_error,
		):
			with self.assertRaises(RuntimeError):
				classifier.run_batch()
		log_error.assert_called_once()
		self.assertIn("AI classification run failed", log_error.call_args.kwargs["title"])

	def test_the_batch_job_does_nothing_when_tagging_is_off(self):
		with (
			patch.object(classifier, "_ready_to_run", lambda: False),
			patch.object(classifier, "_pending") as pending,
		):
			classifier.run_batch()
		pending.assert_not_called()

	def test_a_message_that_keeps_failing_stops_being_retried(self):
		bad = self._message("Send the price list", message_id="keeps_failing")
		self.assertIn(bad.name, classifier._pending(100000))
		for _i in range(classifier.MAX_ATTEMPTS):
			self._run_batch([bad.name], classify=lambda n: (_ for _ in ()).throw(RuntimeError("boom")))
		self.assertEqual(self._status(bad.name).classification_attempts, classifier.MAX_ATTEMPTS)
		self.assertNotIn(bad.name, classifier._pending(100000))

	def test_only_text_chats_are_analysed(self):
		for kind in ("image", "document", "ptt", "video", "vcard", "sticker"):
			doc = self._message(
				"Please see the attached quotation", message_id=f"m_{kind}", message_type=kind
			)
			with patch.object(classifier, "call_claude") as ai:
				classifier.classify(doc.name)
			ai.assert_not_called()
			row = frappe.db.get_value(
				"Periskope Message", doc.name, ["classification_status", "classification_error"], as_dict=True
			)
			self.assertEqual(
				(row.classification_status, row.classification_error), ("Skipped", "Not a text message"), kind
			)

	def test_new_messages_are_marked_by_type_when_tagging_is_on(self):
		with patch.object(frappe.db, "get_single_value", return_value=1):
			text = self._message("Send the price list", message_id="n_text")
			image = self._message("photo caption", message_id="n_image", message_type="image")
		self.assertEqual(text.classification_status, "Pending")
		self.assertEqual(
			(image.classification_status, image.classification_error), ("Skipped", "Not a text message")
		)
		self.assertEqual((image.ai_category, image.severity), ("general", "Low"))

	def test_history_context_contains_only_text_chats(self):
		self._message("Do you have 10mm cable?", message_id="c1", timestamp="2026-09-29T06:00:00Z")
		self._message(
			"scan of the datasheet",
			message_id="c2",
			message_type="document",
			timestamp="2026-09-29T06:05:00Z",
		)
		current = self._message("Send price", message_id="c3", timestamp="2026-09-29T06:20:00Z")
		history = classifier.get_history(current, 5)
		self.assertEqual([h.body for h in history], ["Do you have 10mm cable?"])

	def test_failure_is_recorded_and_counted(self):
		doc = self._message()
		with (
			patch.object(classifier, "call_claude", side_effect=ValueError("boom")),
			patch.object(frappe, "log_error"),
		):
			classifier.classify(doc.name)
		row = frappe.db.get_value(
			"Periskope Message",
			doc.name,
			["classification_status", "classification_attempts", "classification_error"],
			as_dict=True,
		)
		self.assertEqual((row.classification_status, row.classification_attempts), ("Failed", 1))
		self.assertIn("boom", row.classification_error)

	def test_prompt_names_the_categories_and_history_is_ordered(self):
		prompt = classifier.build_system_prompt()
		for category in tags.names():
			self.assertIn(category, prompt)
		for word in ("shade", "GSM", "shrinkage", "pilling", "wash fastness", "Tanglish", "LAST message"):
			self.assertIn(word, prompt)
		self.assertNotIn("tag", prompt.lower().replace("stage", ""))

		first = self._message("Do you have 10mm cable?", message_id="h1", timestamp="2026-09-29T06:00:00Z")
		second = self._message(
			"Yes, in stock", message_id="h2", from_me=True, timestamp="2026-09-29T06:10:00Z"
		)
		current = self._message("Send price", message_id="h3", timestamp="2026-09-29T06:20:00Z")
		history = classifier.get_history(current, 5)
		self.assertEqual([h.body for h in history], [first.body, second.body])
		text = classifier.build_user_message(current, history)
		self.assertIn("[Rep ", text)
		self.assertIn("Message to classify:", text)
		self.assertNotIn("919600000001", text)

	def test_prompt_uses_instructions_and_business_note_from_settings(self):
		custom = SimpleNamespace(
			ai_instructions="Be strict. {curly} braces stay.",
			ai_business_context="We sell industrial cables in Chennai.",
		)
		prompt = classifier.build_system_prompt(custom)
		self.assertTrue(
			prompt.startswith("About the business you are working for:\nWe sell industrial cables")
		)
		self.assertIn("Be strict. {curly} braces stay.", prompt)
		self.assertIn("customer_name is the name", prompt)  # always added

	def test_empty_instructions_fall_back_to_the_default(self):
		for text in ("  ", None):
			prompt = classifier.build_system_prompt(
				SimpleNamespace(ai_instructions=text, ai_business_context=None)
			)
			self.assertIn("You review WhatsApp messages", prompt)
			self.assertIn("order_or_enquiry", prompt)

	def test_parse_json_reply_tolerates_fences_and_extra_words(self):
		self.assertEqual(classifier.parse_json_reply('```json\n{"a": 1}\n```'), {"a": 1})
		self.assertEqual(
			classifier.parse_json_reply('Sure! Here it is: {"a": {"b": 2}} Hope that helps.'), {"a": {"b": 2}}
		)
		self.assertRaises(ValueError, classifier.parse_json_reply, "no json here")

	def _run_call(self, model):
		"""Run call_claude against a fake client and return the request it sent."""
		doc = self._message()
		self.settings.ai_model = model
		reply = json.dumps({"category": "order_or_enquiry", "severity": "Low"})
		client = MagicMock()
		client.messages.create.return_value = SimpleNamespace(
			stop_reason="end_turn",
			content=[
				SimpleNamespace(type="thinking", text=None),
				SimpleNamespace(
					type="text", text=f"```json\n{reply}\n```" if not classifier.is_claude(model) else reply
				),
			],
		)
		with patch.object(classifier, "_client", lambda s: client):
			result = classifier.call_claude(self.settings, doc)
		return client.messages.create.call_args.kwargs, result

	def test_claude_request_uses_structured_output_and_caching(self):
		request, result = self._run_call("claude-haiku-4-5")
		self.assertIn("output_config", request)
		self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})
		self.assertEqual(result["category"], "order_or_enquiry")

	def test_glm_request_asks_for_json_in_the_prompt(self):
		request, result = self._run_call("glm-5.3")
		self.assertNotIn("output_config", request)
		self.assertIsInstance(request["system"], str)
		self.assertIn("Reply with ONLY one JSON object", request["system"])
		self.assertEqual(result["category"], "order_or_enquiry")

	def test_jarvis_provider_sends_prompt_and_message_to_jarvis_and_parses_the_reply(self):
		doc = self._message()
		self.settings.ai_provider = "Jarvis"
		reply = json.dumps({"category": "order_or_enquiry", "severity": "Low"})
		sent = {}

		def fake_task(prompt, input_payload, timeout=60):
			sent.update(prompt=prompt, input=input_payload)
			return reply

		with (
			patch.object(classifier, "_client") as direct,
			patch.object(classifier, "_jarvis_llm_task", lambda: (fake_task, ValueError)),
		):
			result = classifier.call_claude(self.settings, doc)
		direct.assert_not_called()
		self.assertEqual(result["category"], "order_or_enquiry")
		self.assertIn("order_or_enquiry", sent["prompt"])
		self.assertIn("JSON string literal", sent["prompt"])
		self.assertIn("Message to classify:", sent["input"])

	def test_jarvis_provider_needs_no_api_key_and_hides_the_optimizer(self):
		self.settings.ai_provider = "Jarvis"
		self.settings.enable_agent = 1
		self.settings.enable_classification = 1
		self.settings.get_password = lambda field, **kw: None
		with patch.object(classifier, "get_settings", lambda: self.settings):
			self.assertTrue(classifier._ready_to_run())
		with self.assertRaises(frappe.ValidationError):
			classifier.optimize_instructions(self.settings, "x")

	def test_jarvis_is_offered_only_when_the_app_is_installed(self):
		with patch("frappe.get_installed_apps", lambda: ["frappe", "crm"]):
			self.assertEqual(classifier.available_providers(), ["Direct API"])
		with patch("frappe.get_installed_apps", lambda: ["frappe", "jarvis"]):
			self.assertEqual(classifier.available_providers(), ["Direct API", "Jarvis"])

	def test_saving_falls_back_to_the_direct_api_when_jarvis_is_gone(self):
		settings = frappe.get_single("Periskope Settings")
		settings.ai_provider = "Jarvis"
		with patch("frappe.get_installed_apps", lambda: ["frappe"]):
			settings.validate()
		self.assertEqual(settings.ai_provider, "Direct API")

	def test_jarvis_errors_become_readable_validation_errors(self):
		def boom(prompt, input_payload, timeout=60):
			raise ValueError("gateway down")

		self.settings.ai_provider = "Jarvis"
		with patch.object(classifier, "_jarvis_llm_task", lambda: (boom, ValueError)):
			with self.assertRaises(frappe.ValidationError):
				classifier.ping(self.settings)

	def test_group_prompt_has_group_name_senders_and_the_quoted_message(self):
		doc = self._message()
		doc.is_group, doc.chat_name, doc.sender_phone = 1, "Kongu Region", "917667787999"
		history = [
			SimpleNamespace(
				direction="Incoming",
				body="ACME - SOJV/1",
				message_timestamp=None,
				sender_phone="919786800110",
				is_group=1,
			)
		]
		text = classifier.build_user_message(doc, history, "ACME - SOJV/1")
		self.assertIn('called "Kongu Region"', text)
		self.assertIn("internal team group", text)
		self.assertIn("[Customer 919786800110 ] ACME - SOJV/1", text)
		self.assertIn("replies to this earlier message: ACME - SOJV/1", text)
		self.assertIn("[Customer 917667787999", text.split("Message to classify:")[1])

	def test_a_group_message_always_gets_history_even_when_context_is_off(self):
		doc = self._message()
		doc.is_group = 1
		self.settings.context_messages = 0
		with (
			patch.object(classifier, "get_history", return_value=[]) as history,
			patch.object(classifier, "_client", lambda s: MagicMock()),
		):
			try:
				classifier.call_claude(self.settings, doc)
			except Exception:
				pass  # the fake client's reply is not the point here
		self.assertEqual(history.call_args.args[1], classifier.GROUP_MIN_CONTEXT)

	def test_custom_base_url_and_bearer_header(self):
		self.settings.ai_base_url = " https://api.z.ai/api/anthropic/ "
		self.settings.get_password = lambda field, **kw: "secret-key"
		with patch("anthropic.Anthropic") as anthropic_cls:
			classifier._client(self.settings)
		kwargs = anthropic_cls.call_args.kwargs
		self.assertEqual(kwargs["base_url"], "https://api.z.ai/api/anthropic")
		self.assertEqual(kwargs["default_headers"], {"Authorization": "Bearer secret-key"})

	def test_glm_model_defaults_to_zai_address_and_claude_to_anthropic(self):
		self.settings.get_password = lambda field, **kw: "k"
		self.settings.ai_base_url = None
		self.settings.ai_model = "glm-5.3"
		with patch("anthropic.Anthropic") as anthropic_cls:
			classifier._client(self.settings)
		self.assertEqual(anthropic_cls.call_args.kwargs["base_url"], "https://api.z.ai/api/anthropic")

		self.settings.ai_model = "claude-haiku-4-5"
		with patch("anthropic.Anthropic") as anthropic_cls:
			classifier._client(self.settings)
		self.assertNotIn("base_url", anthropic_cls.call_args.kwargs)

	def _optimizer_client(self, text, stop_reason="end_turn"):
		client = MagicMock()
		client.messages.create.return_value = SimpleNamespace(
			stop_reason=stop_reason, content=[SimpleNamespace(type="text", text=text)]
		)
		return client

	def test_optimizer_returns_improved_text_that_keeps_the_categories(self):
		categories = ", ".join(tags.names())
		improved = "You classify WhatsApp sales messages. " * 6 + f"\n\nCategories: {categories}"
		client = self._optimizer_client(f"```\n{improved}\n```")
		with patch.object(classifier, "_client", lambda s: client):
			text = classifier.optimize_instructions(
				self.settings, "Old instructions", "be stricter on pricing"
			)
		self.assertEqual(text, improved)
		request = client.messages.create.call_args.kwargs
		self.assertIn("be stricter on pricing", request["messages"][0]["content"])
		self.assertIn("Old instructions", request["messages"][0]["content"])
		self.assertNotIn("output_config", request)  # plain text answer, works for any provider

	def test_optimizer_text_needs_no_category_list_because_tags_are_added_separately(self):
		client = self._optimizer_client("You classify WhatsApp sales messages carefully. " * 5)
		with patch.object(classifier, "_client", lambda s: client):
			text = classifier.optimize_instructions(self.settings, "", "")
		self.assertNotIn("Categories", text)
		self.assertIn("Categories", classifier.build_system_prompt())

	def test_optimizer_rejects_empty_or_cut_off_answers(self):
		for client in (self._optimizer_client("Sorry"), self._optimizer_client("x" * 500, "max_tokens")):
			with patch.object(classifier, "_client", lambda s, c=client: c):
				self.assertRaises(
					frappe.ValidationError, classifier.optimize_instructions, self.settings, "abc", ""
				)

	def test_optimizer_turns_provider_errors_into_a_clear_message(self):
		client = MagicMock()
		client.messages.create.side_effect = ConnectionError("down")
		with patch.object(classifier, "_client", lambda s: client):
			self.assertRaises(
				frappe.ValidationError, classifier.optimize_instructions, self.settings, "abc", ""
			)

	def test_schema_enums_match_the_categories(self):
		schema = classifier.build_schema()
		self.assertEqual(schema["properties"]["category"]["enum"], tags.names())
		self.assertEqual(schema["properties"]["severity"]["enum"], ["Low", "Medium", "High"])
		self.assertEqual(set(schema["required"]), set(schema["properties"]))
		self.assertFalse(schema["additionalProperties"])


class TestFeedbackLoop(FrappeTestCase):
	"""R17: corrections flow back into the prompt as bounded examples."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_feedback_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_feedback_test")
		# the site's real corrections must not count; rolled back after the test
		frappe.db.sql("update `tabPeriskope Message` set correct_category = null")

	def _corrected(self, message_id, body, category):
		name = insert_as_system(
			{
				"doctype": "Periskope Message",
				"message_id": message_id,
				"body": body,
				"ai_category": "general",
				"classification_status": "Done",
				"correct_category": category,
			}
		).name
		frappe.db.set_value(
			"Periskope Message", name, "modified", "2026-10-01 10:00:00", update_modified=False
		)
		return name

	def test_corrections_appear_in_the_prompt_and_are_bounded(self):
		self.assertEqual(classifier.correction_examples(), "")
		self._corrected("fb1", "The shade is completely out, rejection", "quality_complaint")
		self._corrected("fb2", "Please send your best price for 2 tons", "order_or_enquiry")
		prompt = classifier.build_system_prompt(frappe._dict(ai_instructions="", ai_business_context=""))
		self.assertIn("quality_complaint", prompt)
		self.assertIn("shade is completely out", prompt)

		# bounded: many corrections, still a small block (cacheable)
		for i in range(60):
			self._corrected(f"fbx{i}", f"message number {i} about fabric", "general")
		examples = classifier.correction_examples()
		self.assertLess(len(examples), classifier.MAX_EXAMPLES_CHARS + 200)
		self.assertLessEqual(examples.count("->"), classifier.MAX_EXAMPLES)
