"""Security and spec guards: no outbound messaging anywhere, the disabled integration gate,
the webhook's response-time budget, and the AI cost controls (feedback loop, budget, tokens)."""

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import messages
from periskope_connector.ai import classifier
from periskope_connector.api import webhook
from periskope_connector.system import insert_as_system

APP = Path(__file__).resolve().parent.parent


class TestNoOutboundMessaging(FrappeTestCase):
	"""The hard boundary: nothing in this app may send a WhatsApp message, ever."""

	# Periskope's send-message REST surface; none of these may appear anywhere in the app.
	SEND_MARKERS = ("messages/send", "send_message", "send-message", "chats/send", "/sendmessage")

	def test_no_send_message_call_exists_in_the_app(self):
		for path in APP.rglob("*.py"):
			if "__pycache__" in str(path) or path.name.startswith("test_"):
				continue
			source = path.read_text()
			for marker in self.SEND_MARKERS:
				self.assertNotIn(marker, source, f"{path} looks like it sends messages")

	def test_the_periskope_client_never_posts(self):
		source = (APP / "client.py").read_text()
		self.assertIn('"GET"', source)  # reads chats and phones only
		for method in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
			self.assertNotIn(method, source)


class TestDisabledIntegration(FrappeTestCase):
	def _receive(self, body: bytes):
		frappe.local.request = MagicMock()
		frappe.local.request.get_data.return_value = body
		frappe.local.request_ip = "203.0.113.9"
		with patch.object(webhook.frappe, "get_request_header", side_effect=lambda k, d=None: None):
			return webhook.receive()

	def test_a_disabled_integration_stores_nothing_and_fails_loudly(self):
		body = json.dumps({"event_type": "message.created", "data": {}}).encode()
		before = frappe.db.count("Periskope Webhook Log")
		with patch.object(frappe.db, "get_single_value", lambda *a, **k: 0):
			with self.assertRaises(frappe.ValidationError):
				self._receive(body)
		self.assertEqual(frappe.db.count("Periskope Webhook Log"), before)


class TestWebhookTimeBudget(FrappeTestCase):
	"""R22: the webhook answers in well under 2 s and does no heavy work inline."""

	def test_receive_returns_fast_and_enqueues_the_rest(self):
		body = json.dumps(
			{
				"event_type": "message.created",
				"data": {"message_id": "timing_1", "chat_id": "919600000001@c.us", "body": "price?"},
			}
		).encode()
		frappe.local.request = MagicMock()
		frappe.local.request.get_data.return_value = body
		frappe.local.request_ip = "203.0.113.10"
		headers = {}

		def fake_secret(doctype, field, *args, **kw):
			return "secret" if field == "webhook_signing_secret" else 1

		import hashlib
		import hmac as hmac_lib

		headers["x-periskope-signature"] = hmac_lib.new(b"secret", body, hashlib.sha256).hexdigest()
		with (
			patch.object(frappe.db, "get_single_value", side_effect=fake_secret),
			patch("frappe.model.document.Document.get_password", lambda self, f, **kw: "secret"),
			patch.object(
				webhook.frappe, "get_request_header", side_effect=lambda k, d=None: headers.get(k, d)
			),
			patch.object(webhook.frappe, "enqueue") as enqueue,
			patch.object(frappe.db, "commit"),
		):
			started = time.monotonic()
			self.assertEqual(webhook.receive(), {"ok": True})
			elapsed = time.monotonic() - started
		self.assertLess(elapsed, 2.0)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.kwargs["queue"], "short")


class TestFeedbackLoop(FrappeTestCase):
	"""R17: corrections flow back into the prompt as bounded examples."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_feedback_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_feedback_test")

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


class TestCostControls(FrappeTestCase):
	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_cost_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_cost_test")

	def test_the_max_tokens_setting_is_used(self):
		settings = frappe._dict(ai_max_tokens=777)
		self.assertEqual(classifier.max_tokens(settings), 777)
		self.assertEqual(classifier.max_tokens(frappe._dict()), classifier.DEFAULT_MAX_TOKENS)

	def test_run_batch_stops_when_the_budget_is_spent(self):
		from periskope_connector import health

		with (
			patch.object(classifier, "_ready_to_run", lambda: True),
			patch.object(health, "budget_exceeded", lambda: True),
			patch.object(classifier, "_pending", side_effect=AssertionError("should not classify")),
		):
			classifier.run_batch()  # must return quietly without touching _pending

	def test_min_length_skips_tiny_messages(self):
		settings = frappe._dict(min_length=5, classify_group_chats=1)
		doc = frappe._dict(direction="Incoming", message_type="text", is_group=0, body="hi")
		self.assertEqual(classifier.skip_reason(doc, settings), "Too short to tag")
		doc.body = "hello there"
		self.assertIsNone(classifier.skip_reason(doc, settings))
