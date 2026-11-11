"""Tests for the Periskope Webhook Log doctype and the code that works on it."""

import json
import time
from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector.api import webhook


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
