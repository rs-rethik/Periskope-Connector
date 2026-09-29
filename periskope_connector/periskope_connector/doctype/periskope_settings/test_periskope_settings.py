import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from periskope_connector.api import settings as settings_api
from periskope_connector.api import webhook

SECRET = "test-secret"


def _sign(body: bytes) -> str:
	return hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def _response(status=200, body=None):
	r = MagicMock()
	r.status_code = status
	r.ok = status < 400
	r.text = json.dumps(body)
	r.json.return_value = body
	return r


class TestPeriskopeSettings(FrappeTestCase):
	"""Tests run against the real site DB, so they must never write the site's
	API key / secret and must never commit (the webhook commits before rejecting)."""

	def setUp(self):
		fake_secrets = {"api_key": "key", "webhook_signing_secret": SECRET}
		for p in (
			patch("frappe.model.document.Document.get_password", lambda self, f, **kw: fake_secrets.get(f)),
			patch.object(frappe.db, "commit"),
		):
			p.start()
			self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_test")

	def test_client_rejects_bad_key(self):
		with patch("periskope_connector.client.requests.request", return_value=_response(401, {})):
			self.assertRaises(frappe.ValidationError, settings_api._fetch_phones)

	def test_client_network_error(self):
		with patch("periskope_connector.client.requests.request", side_effect=requests.ConnectionError()):
			self.assertRaises(frappe.ValidationError, settings_api._fetch_phones)

	def test_sync_phones_keeps_rep(self):
		phones = [
			{
				"phone_id": "phone-1",
				"phone_name": "Rep A",
				"org_phone": "919876512340@c.us",
				"wa_state": "CONNECTED",
				"is_ready": True,
			}
		]
		with patch("periskope_connector.client.requests.request", return_value=_response(200, phones)):
			rows = settings_api.sync_phones()
			self.assertEqual(rows[0]["number"], "919876512340")

			s = frappe.get_single("Periskope Settings")
			s.phones[0].rep = "Administrator"
			s.save()

			rows = settings_api.sync_phones()
			self.assertEqual(rows[0]["rep"], "Administrator")

	def _call_receive(self, body: bytes, signature: str | None):
		headers = {"x-periskope-signature": signature} if signature else {}
		frappe.local.request = MagicMock()
		frappe.local.request.get_data.return_value = body
		with patch.object(
			webhook.frappe, "get_request_header", side_effect=lambda k, d=None: headers.get(k, d)
		):
			return webhook.receive()

	def test_webhook_valid_signature(self):
		body = json.dumps({"event_type": "message.created", "data": {}}).encode()
		self.assertEqual(self._call_receive(body, _sign(body)), {"ok": True})
		log = frappe.get_last_doc("Periskope Webhook Log")
		self.assertEqual((log.event_type, log.signature_valid), ("message.created", 1))

	def test_webhook_bad_or_missing_signature(self):
		body = b'{"event_type": "message.created"}'
		self.assertRaises(frappe.AuthenticationError, self._call_receive, body, "bad")
		self.assertRaises(frappe.AuthenticationError, self._call_receive, body, None)
