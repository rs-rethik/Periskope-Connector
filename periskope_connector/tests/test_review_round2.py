"""Regression tests for the second code review: sends that are never doubled, records that are
never doubled, test mode on every CRM write, and what a link or a label change may touch."""

from unittest.mock import patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from periskope_connector import (
	attachments,
	calls,
	chat,
	leads,
	linking,
	messages,
	safety,
	scheduled,
	sla,
	timeline_comments,
	workflow,
)
from periskope_connector.ai import classifier
from periskope_connector.api import broadcast, chat_actions, compose, crm_panel, desk_pages, help_bot
from periskope_connector.api import group_settings as gs
from periskope_connector.tests.test_compose import CUSTOMER, A, B, ComposeCase, _ok
from periskope_connector.tests.test_crm_panel import PanelCase, _lead
from periskope_connector.tests.test_workflow import FlowCase
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = [
	*OTHER_APP_DOCTYPES,
	"Periskope Team",
	"Periskope SLA Policy",
]

STRANGER = "919600077777"


def _dry_runs(action: str, chat_id: str) -> int:
	return frappe.db.count(
		"Periskope Agent Action", {"action": action, "mode": safety.DRY_RUN, "chat_id": chat_id}
	)


class TestSendOnce(ComposeCase):
	def _echo(self, body="On it", unique_id="UIDT"):
		return messages.create_message(
			{
				"event_type": "message.created",
				"data": {
					"message_id": f"true_{CUSTOMER}@c.us_{unique_id}",
					"chat_id": f"{CUSTOMER}@c.us",
					"from_me": True,
					"org_phone": f"{A}@c.us",
					"sender_phone": f"{A}@c.us",
					"body": body,
					"ack": "1",
				},
			}
		)

	def test_a_send_that_timed_out_but_went_out_is_adopted_not_doubled(self):
		mine = self._incoming(A)
		self.request.side_effect = requests.ReadTimeout()
		row = compose.send(mine, "On it")
		self.assertEqual(row.send_status, "Failed")
		before = frappe.db.count("Periskope Message", {"periskope_chat": mine})
		self.assertIsNone(self._echo())  # Periskope had taken it: its copy fills in the failed row
		self.assertEqual(frappe.db.count("Periskope Message", {"periskope_chat": mine}), before)
		saved = frappe.db.get_value(
			"Periskope Message", row.name, ["message_id", "send_status", "send_error"], as_dict=True
		)
		self.assertEqual(
			(saved.message_id, saved.send_status, saved.send_error or ""),
			(f"true_{CUSTOMER}@c.us_UIDT", "Queued", ""),
		)
		self.request.reset_mock(side_effect=True)
		self.assertRaises(frappe.ValidationError, compose.retry, row.name)  # sent: nothing to retry
		self.request.assert_not_called()

	def test_a_copy_that_arrives_during_a_timed_out_send_keeps_it_sent(self):
		mine = self._incoming(A)

		def timeout(*args, **kwargs):
			self._echo()
			raise requests.ReadTimeout()

		self.request.side_effect = timeout
		row = compose.send(mine, "On it")
		self.assertEqual(row.send_status, "Queued")
		self.assertEqual(row.message_id, f"true_{CUSTOMER}@c.us_UIDT")

	def test_a_failed_message_is_taken_for_retry_only_once(self):
		mine = self._incoming(A)
		self.request.side_effect = requests.ConnectionError()
		row = compose.send(mine, "On it")
		self.request.reset_mock(side_effect=True)
		self.request.return_value = _ok("UID2")
		self.assertTrue(compose._claim_retry(row.name))  # the first click, or the every-minute job
		self.assertFalse(compose._claim_retry(row.name))
		self.assertRaises(frappe.ValidationError, compose.retry, row.name)  # the second click
		frappe.db.set_value(
			"Periskope Message",
			row.name,
			"modified",
			add_to_date(now_datetime(), minutes=-5),
			update_modified=False,
		)
		self.assertEqual(compose.retry_failed(), 0)
		self.request.assert_not_called()

	def test_the_job_skips_a_message_the_user_is_retrying(self):
		mine = self._incoming(A)
		self.request.side_effect = requests.ConnectionError()
		row = compose.send(mine, "On it")
		self.request.reset_mock(side_effect=True)
		frappe.db.set_value(
			"Periskope Message",
			row.name,
			"modified",
			add_to_date(now_datetime(), minutes=-5),
			update_modified=False,
		)
		with patch.object(compose, "_claim_retry", return_value=False):  # the user's Retry won
			self.assertEqual(compose.retry_failed(), 0)
		self.request.assert_not_called()


class TestOneChatOneLead(PanelCase):
	def test_a_first_message_that_loses_the_race_for_the_chat_still_joins_it(self):
		first = self._msg()
		real = chat.name_of
		answers = [None]  # this message looked before the other one's chat row was there

		def name_of(number, chat_id):
			return answers.pop() if answers else real(number, chat_id)

		with patch.object(chat, "name_of", name_of):
			name = messages.create_message(
				event(
					message_id=f"false_{CUSTOMER}@c.us_RACE",
					chat_id=f"{CUSTOMER}@c.us",
					sender_phone=f"{CUSTOMER}@c.us",
					org_phone=f"{A}@c.us",
					body="second",
				)
			)
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "periskope_chat"), first)
		self.assertEqual(frappe.db.count("Periskope Chat", {"chat_id": f"{CUSTOMER}@c.us"}), 1)
		self.assertEqual(frappe.db.get_value("Periskope Chat", first, "message_count"), 2)

	def test_a_duplicate_event_undoes_only_itself(self):
		self._msg()
		payload = event(
			message_id=f"false_{CUSTOMER}@c.us_DUP",
			chat_id=f"{CUSTOMER}@c.us",
			sender_phone=f"{CUSTOMER}@c.us",
			org_phone=f"{A}@c.us",
		)
		saved = messages.create_message(payload)
		marker = frappe.get_doc({"doctype": "ToDo", "description": "work not committed yet"}).insert(
			ignore_permissions=True
		)
		real = frappe.db.exists

		def exists(doctype, *args, **kwargs):
			if doctype == "Periskope Message":
				return None  # the two deliveries were checked at the same moment
			return real(doctype, *args, **kwargs)

		with patch.object(frappe.db, "exists", exists):
			self.assertIsNone(messages.create_message(payload))
		self.assertTrue(real("ToDo", marker.name))
		self.assertTrue(real("Periskope Message", saved))

	def _enquiry(self, number=STRANGER):
		return frappe._dict(
			direction="Incoming",
			is_group=0,
			chat_id=f"{number}@c.us",
			contact_number=number,
			contact_name="Review Buyer",
			rep=None,
		)

	def test_a_lead_another_job_is_making_is_not_made_twice(self):
		settings(test_mode=0)
		doc = self._enquiry()
		held = frappe.cache().lock(
			frappe.cache().make_key(f"periskope_new_lead:{linking.number_key(STRANGER)}"), timeout=30
		)
		self.assertTrue(held.acquire(blocking=False))
		self.addCleanup(lambda: held.owned() and held.release())
		with patch.object(leads, "LOCK_WAIT_SECONDS", 0.2):
			self.assertIsNone(leads.create_lead(doc))  # the other job holds this number
		self.assertFalse(linking.by_mobile(STRANGER))
		held.release()
		lead = leads.create_lead(doc)
		self.assertTrue(lead)
		# the job that waited looks again once it has the lock, and finds the lead
		with patch.object(leads, "by_mobile", return_value=[]):
			self.assertIsNone(leads.create_lead(doc))
		self.assertEqual(frappe.db.count("CRM Lead", {"mobile_no": f"+{STRANGER}"}), 1)


class TestTestModeCrm(PanelCase):
	def setUp(self):
		super().setUp()
		settings(test_mode=1, test_allowed_chats=CUSTOMER)

	def test_no_lead_for_a_chat_that_is_not_a_test_number(self):
		doc = frappe._dict(
			direction="Incoming",
			is_group=0,
			chat_id=f"{STRANGER}@c.us",
			contact_number=STRANGER,
			contact_name="",
			rep=None,
		)
		self.assertIsNone(leads.create_lead(doc))
		self.assertIsNone(leads.create_lead(doc))
		self.assertFalse(frappe.db.exists("CRM Lead", {"mobile_no": f"+{STRANGER}"}))
		self.assertEqual(_dry_runs("Create Lead", f"{STRANGER}@c.us"), 1)  # logged once

	def _on_timeline(self, contact):
		lead = _lead("Timeline", contact)
		chat_name = self._msg(contact=contact)
		message = frappe.db.get_value("Periskope Message", {"periskope_chat": chat_name})
		frappe.db.set_value("Periskope Message", message, {"on_timeline": 1, "ai_category": "general"})
		return lead, message

	def test_no_timeline_comment_or_file_for_a_chat_that_is_not_a_test_number(self):
		lead, message = self._on_timeline(STRANGER)
		self.assertIsNone(timeline_comments.add_comment(message))
		linking.link_message(message)
		self.assertFalse(frappe.db.get_value("Periskope Message", message, "timeline_comment"))
		self.assertFalse(
			frappe.db.exists(
				"Comment",
				{"reference_doctype": "CRM Lead", "reference_name": lead, "comment_type": "Comment"},
			)
		)
		self.assertEqual(_dry_runs("Timeline", f"{STRANGER}@c.us"), 1)

		file = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "review-order.txt",
				"content": b"order",
				"is_private": 1,
				"attached_to_doctype": "Periskope Message",
				"attached_to_name": message,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Message", message, "attachment_file", file.name)
		self.assertIsNone(attachments.attach_to_record(message))
		self.assertFalse(
			frappe.db.exists("File", {"attached_to_doctype": "CRM Lead", "attached_to_name": lead})
		)
		self.assertEqual(_dry_runs("Attach File", f"{STRANGER}@c.us"), 1)

	def test_a_test_numbers_comment_is_written_and_follows_a_new_link(self):
		lead, message = self._on_timeline(CUSTOMER)
		linking.link_message(message)
		comment = frappe.db.get_value("Periskope Message", message, "timeline_comment")
		self.assertEqual(frappe.db.get_value("Comment", comment, "reference_name"), lead)
		other = _lead("Chosen By Hand")
		crm_panel.link_record(self._msg(), "CRM Lead", other)
		self.assertEqual(frappe.db.get_value("Comment", comment, "reference_name"), other)
		self.assertEqual(frappe.db.get_value("Periskope Message", message, "timeline_comment"), comment)

	def test_a_call_logs_its_dry_run_once(self):
		if not frappe.db.exists("DocType", "CRM Call Log"):
			self.skipTest("Frappe CRM is not installed")
		lead = _lead("Caller", STRANGER)
		chat_name = self._msg(contact=STRANGER)
		frappe.db.set_value("Periskope Chat", chat_name, "crm_lead", lead)
		row = frappe._dict(
			name="REVIEWCALL",
			call_id="REVIEWCALL",
			chat_id=f"{STRANGER}@c.us",
			whatsapp_number=A,
			direction="Incoming",
			result="missed",
			call_time=now_datetime(),
			duration=0,
			answered_by=None,
			summary="",
			transcript="",
		)
		for _sync in range(3):
			self.assertIsNone(calls.to_crm(row))
		self.assertEqual(_dry_runs("Call Log", f"{STRANGER}@c.us"), 1)


class TestTestModeSends(PanelCase):
	def setUp(self):
		super().setUp()
		settings(enabled=1, test_mode=1, test_allowed_chats=CUSTOMER)

	def _broadcast(self, name, chat_id, status="Sending"):
		return frappe.get_doc(
			{
				"doctype": "Periskope Broadcast",
				"broadcast_id": name,
				"title": name,
				"whatsapp_number": A,
				"message": "hello",
				"status": status,
				"recipient_count": 1,
				"recipients": frappe.as_json([{"chat": "x", "chat_id": chat_id, "title": "x"}]),
			}
		).insert(ignore_permissions=True)

	def test_turning_test_mode_on_stops_the_broadcasts_to_customers(self):
		frappe.db.delete("Periskope Broadcast", {"status": ["in", ["Scheduled", "Sending"]]})
		self._msg()  # makes the number
		to_customer = self._broadcast("RVB1", f"{STRANGER}@c.us", "Scheduled").name
		to_tester = self._broadcast("RVB2", f"{CUSTOMER}@c.us").name
		with (
			patch.object(broadcast, "request", return_value={"status": "deleted"}) as request,
			patch.object(scheduled, "sync") as sync,
		):
			scheduled.on_test_mode_on()
		sync.assert_called_once()
		self.assertEqual(request.call_args.args[:2], ("DELETE", f"message/broadcast/{to_customer}"))
		self.assertEqual(request.call_count, 1)
		self.assertEqual(frappe.db.get_value("Periskope Broadcast", to_customer, "status"), "Cancelled")
		self.assertEqual(frappe.db.get_value("Periskope Broadcast", to_tester, "status"), "Sending")

	def test_a_group_whose_members_cannot_be_read_is_not_changed(self):
		row = frappe._dict(number=A, chat_id="120363000000011111@g.us")
		self.assertTrue(gs._test_mode_block(row, []))
		self.assertFalse(gs._test_mode_block(row, [{"number": A}, {"number": CUSTOMER}]))
		settings(test_mode=0)
		self.assertFalse(gs._test_mode_block(row, []))


class TestScheduledSync(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_review_scheduled")
		settings(enabled=1, test_mode=0, base_url="https://api.periskope.test/v1")
		frappe.db.delete(scheduled.DOCTYPE)
		self.listed = []
		self.runs = []

		def fake(method, path, **kwargs):
			if path == "message/scheduled":
				return {"scheduled_messages": self.listed}
			return {"occurrences": self.runs}

		p = patch.object(scheduled, "request", side_effect=fake)
		p.start()
		self.addCleanup(p.stop)

	def _item(self, sid, status="scheduled"):
		return {
			"scheduled_id": sid,
			"org_phone": f"{A}@c.us",
			"chat_id": f"{CUSTOMER}@c.us",
			"message_payload": {"message": "hello"},
			"scheduled_at": "2030-01-01T09:00:00.000Z",
			"status": status,
		}

	def test_an_empty_answer_or_a_failed_save_deletes_nothing(self):
		scheduled.save(self._item("KEEP1"))
		scheduled.save(self._item("KEEP2"))
		scheduled.save(self._item("GONE"))
		scheduled.sync()  # Periskope answered with no list at all
		self.assertEqual(frappe.db.count(scheduled.DOCTYPE), 3)
		self.listed = [self._item("KEEP1"), self._item("KEEP2")]
		real = scheduled.save

		def save(item, **kwargs):
			if item["scheduled_id"] == "KEEP2":
				raise RuntimeError("could not save")
			return real(item, **kwargs)

		with patch.object(scheduled, "save", save):
			scheduled.sync()
		self.assertTrue(frappe.db.exists(scheduled.DOCTYPE, "KEEP2"))  # still in Periskope's list
		self.assertFalse(frappe.db.exists(scheduled.DOCTYPE, "GONE"))

	def test_a_finished_schedule_is_checked_once_more_and_then_left_alone(self):
		scheduled.save(self._item("DONE1", status="completed"))
		old = add_to_date(now_datetime(), days=-2)
		frappe.db.set_value(scheduled.DOCTYPE, "DONE1", "modified", old, update_modified=False)
		self.listed = [self._item("DONE1", status="completed")]
		self.assertEqual(scheduled.sync()["runs_checked"], 0)  # nothing changed: not read again
		modified = frappe.db.get_value(scheduled.DOCTYPE, "DONE1", "modified")
		self.assertEqual(modified.replace(microsecond=0), old.replace(microsecond=0))
		frappe.db.set_value(scheduled.DOCTYPE, "DONE1", "status", "Scheduled", update_modified=False)
		self.assertEqual(scheduled.sync()["runs_checked"], 1)  # it finished since: its last runs
		self.assertEqual(scheduled.sync()["runs_checked"], 0)

	def test_a_sent_run_marks_its_message_by_its_exact_id(self):
		user = frappe.session.user
		ours = messages.create_message(event(message_id="true_919600033333@c.us_RUNID1", from_me=True))
		other = messages.create_message(event(message_id="true_919600033333@c.us_RUNID12", from_me=True))
		scheduled._mark_sent_by(
			"true_919600033333@c.us_RUNID1", frappe._dict(scheduled_by=user, periskope_chat=None)
		)
		self.assertEqual(frappe.db.get_value("Periskope Message", ours, "sent_by"), user)
		self.assertFalse(frappe.db.get_value("Periskope Message", other, "sent_by"))


class TestMessageState(PanelCase):
	def test_the_ai_result_does_not_put_back_what_changed_during_the_call(self):
		chat_name = self._msg()
		name = frappe.db.get_value("Periskope Message", {"periskope_chat": chat_name})
		doc = frappe.get_doc("Periskope Message", name)  # read before the AI call
		frappe.db.set_value("Periskope Message", name, {"ack": "3", "attachment_summary": "An invoice"})
		classifier.apply_result(doc, {"category": "general", "summary": "Asks a price", "needs_reply": True})
		saved = frappe.db.get_value(
			"Periskope Message",
			name,
			["ack", "attachment_summary", "ai_summary", "classification_status", "needs_reply"],
			as_dict=True,
		)
		self.assertEqual((saved.ack, saved.attachment_summary), ("3", "An invoice"))
		self.assertEqual(
			(saved.ai_summary, saved.classification_status, saved.needs_reply), ("Asks a price", "Done", 1)
		)

	def test_messages_waiting_for_the_ai_are_found_by_their_status(self):
		chat_name = self._msg()
		name = frappe.db.get_value("Periskope Message", {"periskope_chat": chat_name})
		old = add_to_date(now_datetime(), minutes=-30)
		for status, waiting in (
			("Pending", True),
			("Failed", True),
			("", True),
			(None, True),
			("Done", False),
		):
			frappe.db.set_value(
				"Periskope Message",
				name,
				{"classification_status": status, "classification_attempts": 0, "modified": old},
				update_modified=False,
			)
			self.assertEqual(name in classifier._pending(100000), waiting, status)
		frappe.db.set_value(
			"Periskope Message",
			name,
			{"classification_status": "Failed", "classification_attempts": classifier.MAX_ATTEMPTS},
			update_modified=False,
		)
		self.assertNotIn(name, classifier._pending(100000))

	def test_a_label_changes_only_the_label(self):
		chat_name = self._msg()
		tag = frappe.db.get_value("Periskope Tag", {})
		old = add_to_date(now_datetime(), days=-1)
		frappe.db.set_value("Periskope Chat", chat_name, "modified", old, update_modified=False)
		with patch("frappe.automation.doctype.assignment_rule.assignment_rule.apply") as rules:
			chat_actions.add_label([chat_name], tag)
			chat_actions.add_label([chat_name], tag)  # there already: once
			self.assertEqual(frappe.get_doc("Periskope Chat", chat_name).labels[0].tag, tag)
			self.assertEqual(len(frappe.get_doc("Periskope Chat", chat_name).labels), 1)
			chat_actions.remove_label([chat_name], tag)
			# the chat itself is not saved, so its assignment rules do not run
			self.assertNotIn("Periskope Chat", [c.args[0].doctype for c in rules.call_args_list])
		self.assertEqual(frappe.get_doc("Periskope Chat", chat_name).labels, [])
		modified = frappe.db.get_value("Periskope Chat", chat_name, "modified")
		self.assertEqual(modified.replace(microsecond=0), old.replace(microsecond=0))
		actions = [a.details for a in chat_actions.activity(chat_name) if a.action == "Label"]
		self.assertEqual(len(actions), 2)

	def test_a_message_of_the_contacts_other_chat_cannot_be_quoted(self):
		on_a = self._msg(A)
		on_b = self._msg(B)
		theirs = frappe.db.get_value("Periskope Message", {"periskope_chat": on_b})
		row = crm_panel._chat(on_a)
		self.assertRaises(frappe.PermissionError, crm_panel._customer_message, row, theirs)
		mine = frappe.db.get_value("Periskope Message", {"periskope_chat": on_a})
		self.assertEqual(crm_panel._customer_message(row, mine).name, mine)


class TestRelink(PanelCase):
	def test_linking_again_does_not_reopen_a_resolved_chat(self):
		chat_name = self._msg()
		workflow.set_status(chat_name, "Resolved")
		message = frappe.db.get_value("Periskope Message", {"periskope_chat": chat_name})
		linking.link_message(message)  # after the AI read it, or a link changed by hand
		row = frappe.db.get_value("Periskope Chat", chat_name, ["status", "waiting_since"], as_dict=True)
		self.assertEqual((row.status, row.waiting_since), ("Resolved", None))
		crm_panel.link_record(chat_name, "CRM Lead", _lead("Relinked"))
		self.assertEqual(frappe.db.get_value("Periskope Chat", chat_name, "status"), "Resolved")

	def test_a_mapped_group_shows_its_customer_on_its_chats(self):
		group_chat = "120363999990077@g.us"
		with patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Buyers"):
			name = messages.create_message(
				event(
					message_id=f"false_{group_chat}_GRP1",
					chat_id=group_chat,
					sender_phone=f"{CUSTOMER}@c.us",
					org_phone=f"{A}@c.us",
				)
			)
		chat_name = frappe.db.get_value("Periskope Message", name, "periskope_chat")
		lead = _lead("Group Buyer")
		desk_pages.map_group(group_chat, group_type="Customer", crm_lead=lead)
		row = frappe.db.get_value(
			"Periskope Chat", chat_name, ["crm_lead", "linked_by_hand", "linked_by"], as_dict=True
		)
		self.assertEqual((row.crm_lead, row.linked_by_hand, row.linked_by), (lead, 1, frappe.session.user))
		self.assertEqual(frappe.db.get_value("Periskope Message", name, "crm_lead"), lead)
		desk_pages.map_group(group_chat, group_type="Internal")
		row = frappe.db.get_value("Periskope Chat", chat_name, ["crm_lead", "linked_by_hand"], as_dict=True)
		self.assertEqual((row.crm_lead, row.linked_by_hand), (None, 0))

	def test_outside_a_test_the_messages_are_linked_in_a_background_job(self):
		chat_name = self._msg()
		self._msg(body="and one more")
		lead = _lead("Background")
		linked = []
		frappe.flags.in_test = False
		self.addCleanup(frappe.flags.update, {"in_test": True})
		with (
			patch.object(linking, "link_message", linked.append),
			patch.object(frappe, "enqueue") as enqueue,
		):
			linking.relink_chats(f"{CUSTOMER}@c.us", {"crm_lead": lead, "linked_by_hand": 1})
		frappe.flags.in_test = True
		self.assertEqual(frappe.db.get_value("Periskope Chat", chat_name, "crm_lead"), lead)  # at once
		self.assertEqual(len(linked), 1)  # the newest message only
		job = enqueue.call_args
		self.assertEqual(job.args[0], "periskope_connector.linking.relink_messages")
		self.assertEqual(
			(job.kwargs["queue"], job.kwargs["enqueue_after_commit"], job.kwargs["chat_id"]),
			("long", True, f"{CUSTOMER}@c.us"),
		)
		self.assertEqual(linking.relink_messages(f"{CUSTOMER}@c.us"), 2)


class TestSlaAlert(FlowCase):
	def test_a_breach_rings_the_bell_once_even_when_the_email_fails(self):
		frappe.get_doc(
			{
				"doctype": "Periskope SLA Policy",
				"policy_name": "Review Fast",
				"is_default": 1,
				"first_response_minutes": 5,
				"use_working_hours": 0,
			}
		).insert(ignore_permissions=True)
		name = self._msg("919600070099", minutes_ago=30)
		config = sla.reminders.get_config()
		config.emails = ["boss@example.com"]
		self.mail.side_effect = frappe.OutgoingEmailError("no outgoing email account")
		with (
			patch.object(sla.reminders, "get_config", return_value=config),
			patch.object(sla, "_bell") as bell,
		):
			self.assertGreaterEqual(sla.check_breaches(), 1)
			self.assertIn(name, [c.args[1] for c in bell.call_args_list])
			bell.reset_mock()
			sla.check_breaches()
			self.assertNotIn(name, [c.args[1] for c in bell.call_args_list])  # told once
		self.assertEqual(frappe.db.get_value("Periskope Chat", name, "breach_alerted"), 1)
		self.assertTrue(frappe.db.exists("Error Log", {"method": ["like", f"%SLA breach of chat {name}%"]}))


class TestHelpBotLimit(FrappeTestCase):
	def test_a_user_may_ask_only_so_often(self):
		isolate(self, "periskope_review_help_bot")
		key = frappe.cache.make_key(f"periskope:help_bot:asks:{frappe.session.user}")
		frappe.cache.delete(key)
		self.addCleanup(frappe.cache.delete, key)
		with (
			patch.object(help_bot, "ASK_LIMIT", 2),
			patch.object(help_bot.assistant, "readiness", return_value=(False, "no_key")),
		):
			help_bot.ask("How do I add a label?")
			help_bot.ask("And remove it?")
			self.assertRaises(frappe.RateLimitExceededError, help_bot.ask, "And again?")
			self.assertRaises(frappe.ValidationError, help_bot.ask, " ")  # nothing asked: not counted
