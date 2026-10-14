"""Tests for the Periskope Agent Action log and the test-mode guard (safety.py, outbound.py)."""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import client, outbound, safety
from periskope_connector.tests.utils import isolate, settings

ALLOWED = "916374955805@c.us"
OTHER = "919600000123@c.us"
GROUP = "120363000000000001@g.us"


def _actions(**filters):
	return frappe.get_all(
		"Periskope Agent Action",
		filters=filters,
		fields=["action", "mode", "chat_id", "message_text", "external_id", "origin"],
		order_by="creation desc",
	)


class TestSendGuard(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_safety_test")
		settings(
			test_mode=1, test_allowed_chats="916374955805\n" + GROUP, test_allowed_emails="ok@example.com"
		)
		frappe.db.delete("Periskope Agent Action")
		self.http = patch.object(client.requests, "request").start()
		self.addCleanup(patch.stopall)
		self.http.return_value = MagicMock(
			status_code=200, ok=True, json=lambda: {"status": "queued", "unique_id": "u-1"}
		)

	def test_modes(self):
		self.assertEqual(safety.send_mode(ALLOWED), safety.LIVE)
		self.assertEqual(safety.send_mode("+91 63749 55805"), safety.LIVE)
		self.assertEqual(safety.send_mode(OTHER), safety.DRY_RUN)
		self.assertEqual(safety.send_mode(GROUP), safety.BLOCKED)  # listed, still blocked
		self.assertEqual(safety.send_mode(""), safety.BLOCKED)
		settings(test_mode=0)
		self.assertEqual(safety.send_mode(OTHER), safety.LIVE)
		self.assertEqual(safety.send_mode(GROUP), safety.LIVE)

	def test_test_mode_is_on_when_never_saved(self):
		frappe.db.delete("Singles", {"doctype": "Periskope Settings", "field": "test_mode"})
		self.assertTrue(safety.test_mode())

	def test_dry_run_makes_no_request_and_is_logged(self):
		with self.assertRaises(safety.SendNotAllowed) as caught:
			outbound.send(OTHER, "Hello", "919786387999", origin="Auto Reply")
		self.assertEqual(caught.exception.mode, safety.DRY_RUN)
		self.http.assert_not_called()
		row = _actions(chat_id=OTHER)[0]
		self.assertEqual((row.action, row.mode, row.message_text), ("Send Message", "Dry Run", "Hello"))

	def test_groups_are_blocked_without_a_request(self):
		with self.assertRaises(safety.SendNotAllowed):
			outbound.send(GROUP, "Hi all", "919786387999", origin="Auto Reply")
		self.http.assert_not_called()
		self.assertEqual(_actions(chat_id=GROUP)[0].mode, "Blocked")

	def test_calling_the_client_directly_is_also_guarded(self):
		with self.assertRaises(safety.SendNotAllowed):
			client.send_message(OTHER, "Hello", "919786387999")
		self.http.assert_not_called()

	def test_an_allowed_chat_is_sent_and_logged_with_the_periskope_id(self):
		with patch.object(
			frappe,
			"get_single",
			return_value=MagicMock(base_url="https://x", get_password=lambda *a, **k: "k"),
		):
			result = outbound.send(ALLOWED, "Hello", "919786387999", origin="Manual Test")
		self.assertEqual(result["unique_id"], "u-1")
		self.assertEqual(self.http.call_args.kwargs["json"], {"chat_id": ALLOWED, "message": "Hello"})
		row = _actions(chat_id=ALLOWED)[0]
		self.assertEqual((row.mode, row.external_id, row.origin), ("Live", "u-1", "Manual Test"))

	def test_our_own_send_is_recognised_by_its_periskope_id(self):
		row = frappe.get_doc(
			{"doctype": "Periskope Auto Reply", "chat_id": ALLOWED, "status": "Holding Sent"}
		).insert()
		safety.log(
			"Send Message",
			safety.LIVE,
			"Sent",
			chat_id=ALLOWED,
			external_id="3EB0AA",
			detail={"auto_reply": row.name},
		)
		echo = {"message_id": f"true_{ALLOWED}_3EB0AA", "body": "a different text"}
		self.assertEqual(outbound.match_sent(ALLOWED, echo), "Holding")
		self.assertEqual(outbound.match_sent(ALLOWED, {"unique_id": "3EB0AA"}), "Holding")
		self.assertEqual(outbound.match_sent(ALLOWED, {"message_id": f"true_{ALLOWED}_OTHER"}), "")

	def test_crm_writes_run_only_for_allowed_chats(self):
		done = []
		safety.crm_write(OTHER, "Create Note", "Note", lambda: done.append(1))
		self.assertEqual(done, [])
		self.assertEqual(_actions(chat_id=OTHER)[0].mode, "Dry Run")
		safety.crm_write(ALLOWED, "Create Note", "Note", lambda: done.append(1))
		self.assertEqual(done, [1])


class TestEmailGuard(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("periskope_email_guard")
		self.addCleanup(frappe.db.rollback, save_point="periskope_email_guard")
		settings(test_mode=1, test_allowed_emails="ok@example.com")

	def _queue(self, *recipients):
		doc = frappe.get_doc(
			{
				"doctype": "Email Queue",
				"sender": "a@example.com",
				"message": "Subject: Hello\n\nBody",
				"recipients": [{"recipient": r} for r in recipients],
			}
		)
		safety.filter_email_queue(doc)
		return doc

	def test_only_allowed_recipients_stay(self):
		doc = self._queue("ok@example.com", "customer@example.com")
		self.assertEqual([r.recipient for r in doc.recipients], ["ok@example.com"])
		self.assertNotEqual(doc.status, "Error")

	def test_an_email_with_nobody_allowed_is_never_sent(self):
		doc = self._queue("customer@example.com")
		self.assertEqual(doc.recipients, [])
		self.assertEqual(doc.status, "Error")

	def test_nothing_changes_when_test_mode_is_off(self):
		settings(test_mode=0)
		doc = self._queue("customer@example.com")
		self.assertEqual([r.recipient for r in doc.recipients], ["customer@example.com"])
