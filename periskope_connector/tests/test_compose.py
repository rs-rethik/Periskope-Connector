"""Tests for writing from the WhatsApp inbox (phase b): replies always go out from the chat's own
number through the test-mode guard, Periskope's copy fills in the saved row, failed sends are
retried only when that is safe, and notes, quick replies and new chats."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from periskope_connector import chat, messages, numbers
from periskope_connector.api import compose, inbox
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, event, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

A = "919600011111"
B = "919600022222"
CUSTOMER = "919600033333"
OTHER = "919600044444"


def _ok(unique_id="UID1"):
	return SimpleNamespace(
		status_code=200, ok=True, json=lambda: {"status": "queued", "unique_id": unique_id}, text=""
	)


def _user(email, *roles):
	if not frappe.db.exists("User", email):
		frappe.get_doc(
			{"doctype": "User", "email": email, "first_name": email.split("@")[0], "send_welcome_email": 0}
		).insert(ignore_permissions=True)
	frappe.get_doc("User", email).add_roles(*roles)
	return email


class ComposeCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_compose_test")
		settings(
			enabled=1, test_mode=1, test_allowed_chats=CUSTOMER, base_url="https://api.periskope.test/v1"
		)
		for p in (
			patch.object(messages, "fetch_chat_name", lambda *a, **k: ""),
			patch("periskope_connector.autoreply.on_message", lambda doc: ""),
			patch.object(frappe.utils.password, "get_decrypted_password", lambda *a, **k: "key"),
		):
			p.start()
			self.addCleanup(p.stop)
		self.http = patch("periskope_connector.client.requests.request", return_value=_ok())
		self.request = self.http.start()
		self.addCleanup(self.http.stop)
		self.addCleanup(frappe.set_user, "Administrator")
		self.n = 0

	def _incoming(self, number=A, contact=CUSTOMER, body="Price of 30s cotton?"):
		self.n += 1
		messages.create_message(
			event(
				message_id=f"false_{contact}@c.us_IN{self.n}{frappe.generate_hash(length=4)}",
				chat_id=f"{contact}@c.us",
				sender_phone=f"{contact}@c.us",
				org_phone=f"{number}@c.us",
				body=body,
			)
		)
		return chat.name_of(number, f"{contact}@c.us")

	def _sent(self):
		"""The last request to Periskope: (x-phone header, JSON body)."""
		kwargs = self.request.call_args.kwargs
		return kwargs["headers"].get("x-phone"), kwargs["json"]


class TestSending(ComposeCase):
	def test_a_reply_goes_from_the_chats_own_number(self):
		self._incoming(A)
		on_b = self._incoming(B)
		row = compose.send(on_b, "Yes, 500 kg in stock")
		phone, body = self._sent()
		self.assertEqual(phone, B)
		self.assertEqual(body, {"chat_id": f"{CUSTOMER}@c.us", "message": "Yes, 500 kg in stock"})
		self.assertEqual((row.send_status, row.direction, row.whatsapp_number), ("Queued", "Outgoing", B))
		self.assertEqual(frappe.db.get_value("Periskope Message", row.name, "outbound_id"), "UID1")

	def test_test_mode_stops_other_numbers_and_groups_before_any_request(self):
		other = self._incoming(A, OTHER)
		row = compose.send(other, "Hello")
		self.assertEqual(row.send_status, "Not Sent")
		self.request.assert_not_called()
		group = self._incoming(A, "1203639999")  # "@c.us" in the helper; make it a group chat
		frappe.db.set_value("Periskope Chat", group, {"chat_id": "1203639999@g.us", "is_group": 1})
		self.assertEqual(compose.send(group, "Hello team").send_status, "Not Sent")
		self.request.assert_not_called()

	def test_a_rep_reply_cancels_the_waiting_ai_reply(self):
		mine = self._incoming(A)
		frappe.get_doc(
			{
				"doctype": "Periskope Auto Reply",
				"chat_id": f"{CUSTOMER}@c.us",
				"status": "Waiting",
				"due_on": now_datetime(),
			}
		).insert(ignore_permissions=True)
		compose.send(mine, "On it")
		self.assertEqual(
			frappe.db.get_value("Periskope Auto Reply", {"chat_id": f"{CUSTOMER}@c.us"}, "status"),
			"Cancelled",
		)

	def test_a_reply_quotes_a_delivered_message_of_the_same_chat_only(self):
		mine = self._incoming(A)
		quoted = frappe.db.get_value(
			"Periskope Message", {"periskope_chat": mine}, ["name", "message_id"], as_dict=True
		)
		compose.send(mine, "Yes", reply_to=quoted.name)
		self.assertEqual(self._sent()[1]["reply_to"], quoted.message_id)
		local = compose.send(mine, "second")
		frappe.db.set_value("Periskope Message", local.name, "message_id", "local_abc")
		self.assertRaises(frappe.ValidationError, compose.send, mine, "x", reply_to=local.name)
		other = self._incoming(B)
		self.assertRaises(frappe.ValidationError, compose.send, other, "x", reply_to=quoted.name)

	def test_a_file_goes_as_periskope_media_with_its_caption(self):
		mine = self._incoming(A)
		f = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": "rate card.txt",
				"content": b"30s cotton: 245/kg",
				"is_private": 1,
			}
		).insert(ignore_permissions=True)
		row = compose.send(mine, "Our rates", file=f.name)
		media = self._sent()[1]["media"]
		self.assertEqual(
			(media["type"], media["mimetype"], media["filename"]), ("document", "text/plain", "rate card.txt")
		)
		self.assertEqual(base64.b64decode(media["filedata"]), b"30s cotton: 245/kg")
		self.assertEqual(self._sent()[1]["message"], "Our rates")
		self.assertTrue(row.has_media)

	def test_a_new_chat_can_start_with_a_file(self):
		self._incoming(A)  # our number A, as a customer's message makes it
		f = frappe.get_doc(
			{"doctype": "File", "file_name": "catalog.txt", "content": b"30s cotton", "is_private": 1}
		).insert(ignore_permissions=True)
		result = compose.start_chat(A, CUSTOMER, "", file=f.name)
		self.assertEqual(self._sent()[1]["media"]["filename"], "catalog.txt")
		self.assertTrue(result["message"].has_media)
		self.assertRaises(
			frappe.ValidationError, compose.start_chat, A, "919876500099", ""
		)  # nothing to send

	def test_someone_elses_file_cannot_be_sent(self):
		mine = self._incoming(A)
		rep = _user("compose.rep@example.com", "Sales User")
		frappe.get_doc(
			{"doctype": "Periskope Agent", "user": rep, "numbers": [{"whatsapp_number": A}]}
		).insert(ignore_permissions=True)
		frappe.db.set_value("Periskope Chat", mine, "_assign", json.dumps([rep]))
		f = frappe.get_doc(
			{"doctype": "File", "file_name": "secret.txt", "content": b"x", "is_private": 1}
		).insert(ignore_permissions=True)
		frappe.set_user(rep)
		self.assertRaises(frappe.PermissionError, compose.send, mine, "see", file=f.name)

	def test_a_rep_cannot_write_in_a_chat_they_cannot_see(self):
		other = self._incoming(A, OTHER)
		rep = _user("compose.rep@example.com", "Sales User")
		frappe.get_doc(
			{"doctype": "Periskope Agent", "user": rep, "numbers": [{"whatsapp_number": A}]}
		).insert(ignore_permissions=True)
		frappe.set_user(rep)
		self.assertRaises(frappe.PermissionError, compose.send, other, "hi")
		self.assertRaises(frappe.PermissionError, compose.add_comment, other, "note")


class TestEcho(ComposeCase):
	def _echo(self, unique_id="UID1", body="On it", with_unique=True):
		data = {
			"message_id": f"true_{CUSTOMER}@c.us_{unique_id}",
			"chat_id": f"{CUSTOMER}@c.us",
			"from_me": True,
			"org_phone": f"{A}@c.us",
			"sender_phone": f"{A}@c.us",
			"body": body,
			"ack": "1",
		}
		if with_unique:
			data["unique_id"] = unique_id
		return messages.create_message({"event_type": "message.created", "data": data})

	def test_periskopes_copy_fills_in_the_saved_row(self):
		mine = self._incoming(A)
		row = compose.send(mine, "On it")
		before = frappe.db.count("Periskope Message", {"periskope_chat": mine})
		self.assertIsNone(self._echo())
		self.assertEqual(frappe.db.count("Periskope Message", {"periskope_chat": mine}), before)
		saved = frappe.db.get_value(
			"Periskope Message", row.name, ["message_id", "ack", "send_status"], as_dict=True
		)
		self.assertEqual(
			(saved.message_id, saved.ack, saved.send_status), (f"true_{CUSTOMER}@c.us_UID1", "1", "Queued")
		)

	def test_a_copy_that_comes_before_the_send_returned_is_matched_by_its_text(self):
		mine = self._incoming(A)
		row = compose.send(mine, "On it")
		frappe.db.set_value("Periskope Message", row.name, {"outbound_id": None, "send_status": "Sending"})
		self._echo(unique_id="UIDX", with_unique=False)
		self.assertEqual(frappe.db.get_value("Periskope Message", row.name, "outbound_id"), "UIDX")
		self.assertEqual(
			frappe.db.count("Periskope Message", {"message_id": f"true_{CUSTOMER}@c.us_UIDX"}), 1
		)

	def test_ticks_only_go_up_and_minus_one_is_a_failure(self):
		mine = self._incoming(A)
		row = compose.send(mine, "On it")
		ack = lambda value: messages.update_ack({"data": {"unique_id": "UID1", "ack": value}})  # noqa: E731
		ack("3")
		ack("2")  # late and lower: ignored
		self.assertEqual(frappe.db.get_value("Periskope Message", row.name, "ack"), "3")
		ack("-1")
		self.assertEqual(frappe.db.get_value("Periskope Message", row.name, "send_status"), "Failed")

	def test_reactions_are_added_and_taken_away(self):
		mine = self._incoming(A)
		target = frappe.db.get_value(
			"Periskope Message", {"periskope_chat": mine}, ["name", "message_id"], as_dict=True
		)
		react = lambda emoji: messages.update_reaction(  # noqa: E731
			{
				"data": {
					"msg_id": {"_serialized": target.message_id},
					"reaction": emoji,
					"sender_id": f"{A}@c.us",
				}
			}
		)
		react("👍")
		self.assertEqual(inbox.get_messages(mine)["messages"][0].reactions, {A: "👍"})
		react("")
		self.assertEqual(inbox.get_messages(mine)["messages"][0].reactions, {})


class TestTicksWithoutWebhook(ComposeCase):
	def test_the_delivery_status_is_read_from_periskope(self):
		mine = self._incoming(A)
		row = compose.send(mine, "On it")
		asked = []

		def answer(method, url, **kw):
			# The site may hold real replies too: answer each by its own id, change only ours.
			unique_id = url.split("message/")[1].split("/")[0]
			asked.append(unique_id)
			status = {"status": "read", "ack": 4, "message_id": f"true_{CUSTOMER}@c.us_UID1"}
			return SimpleNamespace(
				status_code=200, ok=True, text="", json=lambda: status if unique_id == "UID1" else {}
			)

		self.request.side_effect = answer
		compose.refresh_ticks()
		self.assertIn("UID1", asked)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", row.name, ["ack", "message_id"]),
			("4", f"true_{CUSTOMER}@c.us_UID1"),
		)
		asked.clear()
		compose.refresh_ticks()
		self.assertNotIn("UID1", asked)  # read: not asked again

	def test_opening_a_chat_asks_only_for_its_ticks_and_not_too_often(self):
		mine = self._incoming(A)
		other = self._incoming(B)
		compose.send(mine, "On it")
		self.request.return_value = _ok("UID9")
		compose.send(other, "Other number")
		asked = []
		self.request.side_effect = lambda method, url, **kw: (
			asked.append(url) or SimpleNamespace(status_code=200, ok=True, text="", json=lambda: {"ack": 3})
		)
		frappe.cache().delete_value(f"periskope_ticks:{mine}")
		self.assertEqual(compose.refresh_chat_ticks(mine), 1)
		self.assertEqual([u.split("message/")[1] for u in asked], ["UID1/status"])
		self.assertEqual(compose.refresh_chat_ticks(mine), 0)  # asked again at once: skipped
		rep = _user("compose.rep@example.com", "Sales User")
		frappe.set_user(rep)
		self.assertRaises(frappe.PermissionError, compose.refresh_chat_ticks, mine)


class TestRetry(ComposeCase):
	def test_a_send_that_never_reached_periskope_is_retried_by_itself(self):
		mine = self._incoming(A)
		self.request.side_effect = requests.ConnectionError()
		row = compose.send(mine, "On it")
		self.assertEqual(row.send_status, "Failed")
		self.assertIn("ConnectionError", row.send_error)
		self.request.side_effect = None
		self.request.return_value = _ok("UID2")
		frappe.db.set_value(
			"Periskope Message",
			row.name,
			"modified",
			add_to_date(now_datetime(), minutes=-5),
			update_modified=False,
		)
		self.assertEqual(compose.retry_failed(), 1)
		self.assertEqual(
			frappe.db.get_value("Periskope Message", row.name, ["send_status", "send_attempts"]),
			("Queued", 2),
		)

	def test_a_refused_send_waits_for_the_users_retry(self):
		mine = self._incoming(A)
		self.request.return_value = SimpleNamespace(
			status_code=400, ok=False, text="bad chat", json=lambda: {}
		)
		row = compose.send(mine, "On it")
		frappe.db.set_value(
			"Periskope Message",
			row.name,
			"modified",
			add_to_date(now_datetime(), minutes=-5),
			update_modified=False,
		)
		self.assertEqual(compose.retry_failed(), 0)  # HTTP 400 may have been taken: never doubled
		self.request.return_value = _ok("UID3")
		self.assertEqual(compose.retry(row.name).send_status, "Queued")


class TestNotesAndQuickReplies(ComposeCase):
	def test_a_note_is_never_sent_and_notifies_who_is_mentioned(self):
		mine = self._incoming(A)
		manager = _user("compose.manager@example.com", "Sales Manager")
		stranger = _user("compose.stranger@example.com", "Sales User")
		note = compose.add_comment(mine, "@Manager please call them", mentions=[manager, stranger])
		self.request.assert_not_called()
		self.assertEqual(note["mentions"], [manager])  # the stranger cannot see this number
		self.assertTrue(frappe.db.exists("Notification Log", {"for_user": manager, "document_name": mine}))
		self.assertEqual([n["name"] for n in inbox.get_messages(mine)["notes"]], [note["name"]])

	def test_quick_replies_are_your_own_plus_the_shared_ones(self):
		rep = _user("compose.rep@example.com", "Sales User")
		other = _user("compose.other@example.com", "Sales User")
		shared = compose.save_canned_response(
			"Bank details", "NEFT to ...", is_shared=1
		)  # a manager (Administrator)
		frappe.set_user(other)
		compose.save_canned_response("Mine only", "secret")
		frappe.set_user(rep)
		own = compose.save_canned_response("Hello", "Hi, how can I help?", is_shared=1)
		self.assertEqual(own["is_shared"], 0)  # only managers share
		titles = {r.title for r in compose.canned_responses()}
		self.assertEqual(titles, {"Bank details", "Hello"})
		self.assertRaises(frappe.PermissionError, compose.delete_canned_response, shared["name"])


class TestNewChat(ComposeCase):
	def test_a_rep_starts_a_chat_from_their_number_and_owns_it(self):
		numbers.ensure(A)
		numbers.ensure(B)
		rep = _user("compose.rep@example.com", "Sales User")
		frappe.get_doc(
			{"doctype": "Periskope Agent", "user": rep, "numbers": [{"whatsapp_number": A}]}
		).insert(ignore_permissions=True)
		frappe.set_user(rep)
		self.assertRaises(frappe.PermissionError, compose.start_chat, B, CUSTOMER, "Hello")
		result = compose.start_chat(A, CUSTOMER[2:], "Hello from Pranera")  # 10 digits: +91 added
		self.assertEqual(self._sent()[0], A)
		self.assertEqual(self._sent()[1]["chat_id"], f"{CUSTOMER}@c.us")
		self.assertIn(result["chat"], [c.name for c in inbox.get_chats()["chats"]])
