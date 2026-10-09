"""Webhook set-up: the events the user selected are registered in Periskope, once."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import webhooks

URL = "https://crm.example.com/api/method/periskope_connector.api.webhook.receive"


def _hook(event, url=URL, subscribed=True):
	return {"integration_name": event, "hook_url": url, "is_subscribed": subscribed}


class TestEnsureWebhooks(FrappeTestCase):
	def setUp(self):
		for target in (
			patch.object(webhooks, "webhook_url", lambda: URL),
			patch.object(webhooks, "selected_events", lambda: ["message.created", "message.updated"]),
		):
			target.start()
			self.addCleanup(target.stop)

	def _run(self, existing):
		"""Run with Periskope's current webhooks; returns the result and the POST bodies sent."""
		posted = []

		def fake(method, path, phone=None, json=None):
			if method == "GET":
				return existing
			posted.append(json)
			return [{"integration_name": name} for name in json["integrationName"]]

		with patch.object(webhooks, "request", fake):
			return webhooks.ensure_webhooks(), posted

	def test_all_selected_events_are_created_when_none_exist(self):
		result, posted = self._run([])
		self.assertEqual(
			posted, [{"hookUrl": URL, "integrationName": ["message.created", "message.updated"]}]
		)
		self.assertEqual(result["created"], ["message.created", "message.updated"])

	def test_only_the_missing_event_is_created(self):
		result, posted = self._run([_hook("message.created")])
		self.assertEqual(posted[0]["integrationName"], ["message.updated"])
		self.assertEqual(result["existing"], ["message.created"])

	def test_nothing_is_created_when_everything_exists(self):
		result, posted = self._run([_hook("message.created"), _hook("message.updated")])
		self.assertEqual(posted, [])
		self.assertEqual(result["created"], [])

	def test_an_unsubscribed_webhook_does_not_count(self):
		_, posted = self._run([_hook("message.created", subscribed=False), _hook("message.updated")])
		self.assertEqual(posted[0]["integrationName"], ["message.created"])

	def test_a_webhook_on_another_address_does_not_count_and_is_reported(self):
		old = "https://old-tunnel.ngrok-free.app/api/method/periskope_connector.api.webhook.receive"
		result, posted = self._run([_hook("message.created", url=old), _hook("message.updated", url=old)])
		self.assertEqual(len(posted[0]["integrationName"]), 2)
		self.assertEqual(result["stale"], [old])

	def test_the_response_may_be_wrapped(self):
		_, posted = self._run({"webhooks": [_hook("message.created"), _hook("message.updated")]})
		self.assertEqual(posted, [])

	def test_it_refuses_an_address_periskope_cannot_reach(self):
		for bad in ("http://pranera.site:8002/x", "https://localhost/x", "https://mysite/x"):
			with patch.object(webhooks, "webhook_url", lambda bad=bad: bad):
				with patch.object(webhooks, "request", side_effect=AssertionError("must not call Periskope")):
					with self.assertRaises(frappe.ValidationError, msg=bad):
						webhooks.ensure_webhooks()

	def test_it_asks_for_an_event_when_none_is_selected(self):
		with patch.object(webhooks, "selected_events", lambda: []):
			with patch.object(webhooks, "request", side_effect=AssertionError("must not call Periskope")):
				with self.assertRaises(frappe.ValidationError):
					webhooks.ensure_webhooks()


class TestSelectedEvents(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("periskope_events_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_events_test")
		with patch.object(frappe.db, "commit"):
			settings = frappe.get_doc("Periskope Settings")
			settings.set("webhook_events", [])
			for event in ("message.deleted", "message.created", "message.deleted"):
				settings.append("webhook_events", {"event": event})
			settings.flags.ignore_permissions = True
			settings.save()

	def test_events_come_from_the_settings_without_duplicates_in_order(self):
		self.assertEqual(webhooks.selected_events(), ["message.deleted", "message.created"])


class TestSiteUrl(FrappeTestCase):
	def test_a_configured_host_name_is_used_as_it_is_without_the_dev_port(self):
		with patch.dict(frappe.local.conf, {"host_name": "https://abc.ngrok-free.app/"}):
			self.assertEqual(webhooks.site_url(), "https://abc.ngrok-free.app")
			self.assertEqual(
				webhooks.webhook_url(),
				"https://abc.ngrok-free.app/api/method/periskope_connector.api.webhook.receive",
			)

	def test_a_host_name_without_a_scheme_gets_http(self):
		with patch.dict(frappe.local.conf, {"host_name": "crm.example.com"}):
			self.assertEqual(webhooks.site_url(), "http://crm.example.com")

	def test_without_a_host_name_frappe_decides(self):
		with patch.dict(frappe.local.conf, {"host_name": None}):
			self.assertEqual(webhooks.site_url(), frappe.utils.get_url())
