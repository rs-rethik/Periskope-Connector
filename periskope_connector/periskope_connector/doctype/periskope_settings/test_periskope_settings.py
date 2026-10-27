"""Tests for the Periskope Settings doctype and the code that works on it."""

import hashlib
import hmac
import json
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe
import requests
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, now_datetime

from periskope_connector import flow, health, install, messages, reminders, webhooks
from periskope_connector.ai import classifier
from periskope_connector.api import settings as settings_api
from periskope_connector.api import webhook
from periskope_connector.system import insert_as_system
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, at, cfg

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

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


URL = "https://crm.example.com/api/method/periskope_connector.api.webhook.receive"


def _hook(event, url=URL, subscribed=True):
	return {"integration_name": event, "hook_url": url, "is_subscribed": subscribed}


APP = Path(frappe.get_app_path("periskope_connector"))


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
			self.assertIn("919876512340", [r["number"] for r in rows])

			# What an admin sets on the number survives the next sync.
			frappe.db.set_value(
				"WhatsApp Number",
				"919876512340",
				{"default_assignee": "Administrator", "display_name": "Sales desk"},
			)

			rows = {r["number"]: r for r in settings_api.sync_phones()}
			self.assertEqual(rows["919876512340"]["default_assignee"], "Administrator")
			self.assertEqual(rows["919876512340"]["display_name"], "Sales desk")
			self.assertEqual(rows["919876512340"]["phone_name"], "Rep A")

	def _call_receive(self, body: bytes, signature: str | None):
		headers = {"x-periskope-signature": signature} if signature else {}
		frappe.local.request = MagicMock()
		frappe.local.request.get_data.return_value = body
		frappe.local.request_ip = "203.0.113.7"  # documentation-range address; the rate limiter needs an IP
		real_single = frappe.db.get_single_value

		def single(doctype, field, *a, **kw):
			# the master switch must not turn these signature tests into 503s on a fresh site
			if doctype == "Periskope Settings" and field == "enabled":
				return 1
			return real_single(doctype, field, *a, **kw)

		with (
			patch.object(
				webhook.frappe, "get_request_header", side_effect=lambda k, d=None: headers.get(k, d)
			),
			patch.object(frappe.db, "get_single_value", side_effect=single),
		):
			return webhook.receive()

	def test_saving_the_status_commits_it(self):
		settings_api._set_status("Connected (1 phone(s))")
		frappe.db.commit.assert_called_once()
		self.assertEqual(
			frappe.db.get_single_value("Periskope Settings", "connection_status"), "Connected (1 phone(s))"
		)

	def test_a_commit_error_while_saving_the_status_is_logged_not_raised(self):
		# It runs after a connection error; raising here would hide the error the user needs to see.
		with (
			patch.object(frappe.db, "commit", side_effect=Exception("commit failed")),
			patch.object(frappe.db, "rollback") as rollback,
			patch.object(frappe, "log_error") as log_error,
		):
			settings_api._set_status("Failed: bad key")  # must not raise
		rollback.assert_called_once()
		log_error.assert_called_once()

	def test_webhook_valid_signature(self):
		body = json.dumps({"event_type": "message.created", "data": {}}).encode()
		self.assertEqual(self._call_receive(body, _sign(body)), {"ok": True})
		log = frappe.get_last_doc("Periskope Webhook Log")
		self.assertEqual((log.event_type, log.signature_valid), ("message.created", 1))

	def test_webhook_bad_or_missing_signature_writes_nothing(self):
		body = b'{"event_type": "message.created"}'
		before = frappe.db.count("Periskope Webhook Log")
		self.assertRaises(frappe.AuthenticationError, self._call_receive, body, "bad")
		self.assertRaises(frappe.AuthenticationError, self._call_receive, body, None)
		self.assertRaises(frappe.AuthenticationError, self._call_receive, body, "")
		self.assertEqual(frappe.db.count("Periskope Webhook Log"), before)

	def test_webhook_rejects_everything_when_no_secret_is_configured(self):
		body = b'{"event_type": "message.created"}'
		before = frappe.db.count("Periskope Webhook Log")
		with patch("frappe.model.document.Document.get_password", lambda self, f, **kw: None):
			# even a signature computed with an empty secret must not get through
			self.assertRaises(
				frappe.AuthenticationError,
				self._call_receive,
				body,
				hmac.new(b"", body, hashlib.sha256).hexdigest(),
			)
		self.assertEqual(frappe.db.count("Periskope Webhook Log"), before)

	def test_webhook_accepts_the_signature_in_any_letter_case(self):
		body = json.dumps({"event_type": "chat.created", "data": {}}).encode()
		self.assertEqual(self._call_receive(body, _sign(body).upper()), {"ok": True})

	def test_webhook_only_queues_message_events(self):
		body = json.dumps({"event_type": "chat.created", "data": {}}).encode()
		with patch.object(webhook.frappe, "enqueue") as enqueue:
			self._call_receive(body, _sign(body))
		enqueue.assert_not_called()

	def test_webhook_endpoint_is_rate_limited(self):
		self.assertTrue(hasattr(webhook.receive, "__wrapped__"), "receive() must be wrapped by rate_limit")


class TestWorkingHoursSettings(FrappeTestCase):
	def test_install_stores_the_usual_working_hours_once(self):
		from periskope_connector import install

		frappe.db.set_single_value("Periskope Settings", "working_hours_start", None)
		frappe.db.set_single_value("Periskope Settings", "working_hours_end", None)
		install._ensure_working_hours()
		self.assertEqual(
			str(frappe.db.get_single_value("Periskope Settings", "working_hours_start")), "9:00:00"
		)
		frappe.db.set_single_value("Periskope Settings", "working_hours_end", "18:30:00")
		install._ensure_working_hours()  # an existing value is kept
		self.assertEqual(
			str(frappe.db.get_single_value("Periskope Settings", "working_hours_end")), "18:30:00"
		)

	def test_a_failing_install_step_does_not_skip_the_others(self):
		# A site whose saved settings no longer validate made _ensure_webhook_events fail, and the old
		# single try block then skipped test mode, the defaults and the CRM account.
		names = [
			"_ensure_role",
			"_ensure_custom_fields",
			"_ensure_lead_source",
			"_ensure_tags",
			"_ensure_working_hours",
			"_ensure_webhook_events",
			"_ensure_assistant_defaults",
			"_ensure_crm_ui",
			"_ensure_phone_fields",
		]
		steps = {}
		for name in names:
			mock = MagicMock(
				__name__=name,
				side_effect=frappe.ValidationError("bad hours") if name == "_ensure_webhook_events" else None,
			)
			patcher = patch.object(install, name, mock)
			steps[name] = patcher.start()
			self.addCleanup(patcher.stop)
		with patch.object(frappe, "log_error") as log_error:
			install.setup()
		for step in steps.values():
			step.assert_called_once()
		log_error.assert_called_once_with(title="Periskope: install step _ensure_webhook_events failed")

	def test_a_migrate_keeps_the_switches_an_admin_turned_off(self):
		# The seed-once check never found a stored value, so every migrate switched auto reply and
		# test mode back on.
		frappe.db.savepoint("periskope_seed_once_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_seed_once_test")
		frappe.db.set_single_value(
			"Periskope Settings",
			{"enable_auto_reply": 0, "test_mode": 0, "test_allowed_chats": "919600000001"},
		)
		install._ensure_assistant_defaults()
		value = frappe.db.get_single_value
		self.assertEqual(value("Periskope Settings", "enable_auto_reply"), 0)
		self.assertEqual(value("Periskope Settings", "test_mode"), 0)
		self.assertEqual(value("Periskope Settings", "test_allowed_chats"), "919600000001")
		self.assertTrue(install._stored("test_mode"))
		self.assertFalse(install._stored("no_such_field"))

	def test_a_window_that_is_too_short_is_rejected(self):
		settings = frappe.get_doc("Periskope Settings")
		for start, end in (
			("12:43:41.565946", "12:43:41.565995"),
			("09:00:00", "09:10:00"),
			("10:00:00", "09:00:00"),
		):
			settings.working_hours_start, settings.working_hours_end = start, end
			with self.assertRaises(frappe.ValidationError, msg=f"{start}-{end}"):
				settings.validate_reminders()
		settings.working_hours_start, settings.working_hours_end = "09:00:00", "19:00:00"
		settings.validate_reminders()


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


class TestSyncCheck(FrappeTestCase):
	def setUp(self):
		for target in (
			patch.object(frappe.db, "commit"),
			patch.object(health, "_manager_emails", lambda: ["boss@example.com"]),
		):
			target.start()
			self.addCleanup(target.stop)
		frappe.db.savepoint("periskope_sync_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_sync_test")
		frappe.cache().delete_keys("periskope_alert:*")

	def _phones(self):
		frappe.get_doc(
			{
				"doctype": "WhatsApp Number",
				"number": "919600000001",
				"display_name": "Sales A",
				"default_assignee": "Administrator",
			}
		).insert(ignore_permissions=True)

	def test_a_normally_busy_number_that_goes_silent_alerts(self):
		self._phones()
		counts = {"919600000001": SimpleNamespace(today=0, baseline=5.2)}
		with (
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 1),
			patch.object(health, "phone_counts", lambda: counts),
			patch.object(frappe, "sendmail") as sendmail,
		):
			self.assertEqual(health.sync_check(), ["919600000001"])
			self.assertEqual(health.sync_check(), [])  # once a day, not once a run
			self.assertEqual(sendmail.call_count, 1)
			self.assertIn("unlinked", sendmail.call_args.kwargs["subject"].lower())

	def test_a_quiet_number_is_not_a_problem(self):
		self._phones()
		counts = {"919600000001": SimpleNamespace(today=0, baseline=0.3)}
		with (
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 1),
			patch.object(health, "phone_counts", lambda: counts),
			patch.object(frappe, "sendmail"),
		):
			self.assertEqual(health.sync_check(), [])

	def test_a_number_with_traffic_today_is_fine(self):
		self._phones()
		counts = {"919600000001": SimpleNamespace(today=12, baseline=5.2)}
		with (
			patch.object(frappe.db, "get_single_value", lambda *a, **k: 1),
			patch.object(health, "phone_counts", lambda: counts),
			patch.object(frappe, "sendmail"),
		):
			self.assertEqual(health.sync_check(), [])


class TestGetHealth(FrappeTestCase):
	def test_the_figures_come_back_with_the_expected_keys(self):
		figures = health.get_health()
		for key in (
			"last_webhook",
			"last_ai_run",
			"pending",
			"failed",
			"unprocessed",
			"month_tokens",
			"phones",
		):
			self.assertIn(key, figures)

	def test_the_flags_follow_test_mode_and_the_error_log_not_the_shown_text(self):
		with patch.object(flow.safety, "test_mode", lambda: False):
			figures = health.agent_health()
		self.assertFalse(figures["test_mode_on"])
		self.assertTrue(figures["test_mode"])  # "off" is shown, so the text alone cannot be the flag
		with patch.object(flow.safety, "test_mode", lambda: True):
			self.assertTrue(health.agent_health()["test_mode_on"])
		get_all = frappe.get_all
		no_errors = lambda doctype, *a, **k: [] if doctype == "Error Log" else get_all(doctype, *a, **k)  # noqa: E731
		with patch.object(frappe, "get_all", no_errors):
			figures = health.agent_health()
		self.assertFalse(figures["has_problem"])
		self.assertTrue(figures["last_problem"])


class TestHealthFigures(FrappeTestCase):
	def setUp(self):
		frappe.db.savepoint("periskope_health_figures_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_health_figures_test")

	def test_the_last_webhook_is_the_newest_log(self):
		frappe.get_doc(
			{
				"doctype": "Periskope Webhook Log",
				"event_type": "message.created",
				"signature_valid": 1,
				"payload": "{}",
			}
		).insert(ignore_permissions=True)
		latest = frappe.db.sql("select max(creation) from `tabPeriskope Webhook Log`")[0][0]
		self.assertEqual(health.get_health()["last_webhook"], str(latest))
		self.assertNotEqual(health.get_health()["last_webhook"], "never")


class TestOutboundMessagingBoundary(FrappeTestCase):
	"""The hard boundary: only outbound.py calls client.send_message (the auto reply and the welcome go
	through it), client.send_message runs the test-mode check before any request, and nothing is sent
	while the auto reply is off."""

	SEND_MARKERS = (
		"messages/send",
		"message/send",
		"send_message",
		"send-message",
		"chats/send",
		"/sendmessage",
	)
	ALLOWED = ("client.py", "outbound.py", "autoreply.py", "welcome.py")

	def test_only_the_auto_reply_and_welcome_call_send_message(self):
		for path in APP.rglob("*.py"):
			if "__pycache__" in str(path) or path.name.startswith("test_") or path.name in self.ALLOWED:
				continue
			source = path.read_text()
			for marker in self.SEND_MARKERS:
				self.assertNotIn(marker, source, f"{path} looks like it sends messages")

	def test_the_periskope_client_posts_only_to_send_message(self):
		source = (APP / "client.py").read_text()
		self.assertIn('"GET"', source)
		self.assertEqual(source.count('"POST"'), 1)
		self.assertIn('"POST", "message/send"', source)
		for method in ('"PUT"', '"PATCH"', '"DELETE"'):
			self.assertNotIn(method, source)

	def test_the_client_checks_test_mode_before_any_request(self):
		source = (APP / "client.py").read_text()
		body = source[source.index("def send_message") :]
		self.assertLess(body.index("safety.check_send"), body.index("request("))

	def test_nothing_is_sent_while_auto_reply_is_off(self):
		from periskope_connector import autoreply

		frappe.db.savepoint("periskope_boundary_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_boundary_test")
		frappe.db.set_single_value("Periskope Settings", "enable_auto_reply", 0)
		frappe.get_doc(
			{
				"doctype": "Periskope Auto Reply",
				"chat_id": "919600000999@c.us",
				"status": "Waiting",
				"due_on": "2020-01-01 00:00:00",
			}
		).insert()
		with patch.object(autoreply, "send_message") as send:
			self.assertEqual(autoreply.run_due(), 0)
		send.assert_not_called()


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


class TestFlowTab(FrappeTestCase):
	"""The figures and edits behind the Flow tab. Runs on the real site DB: always rolls back."""

	def setUp(self):
		p = patch.object(frappe.db, "commit")
		p.start()
		self.addCleanup(p.stop)
		frappe.db.savepoint("periskope_flow_test")
		self.addCleanup(frappe.db.rollback, save_point="periskope_flow_test")
		install._ensure_tags()

	def test_every_step_has_a_health_and_a_headline(self):
		data = flow.get_flow()
		steps = {
			"periskope",
			"capture",
			"link",
			"queue",
			"files",
			"ai",
			"tags",
			"decisions",
			"waiting",
			"sla",
			"people",
			"autoreply",
			"crm",
			"agent",
		}
		self.assertEqual(set(data), steps | {"settings"})  # the settings the panels edit
		for name in steps:
			self.assertIn(data[name]["status"], ("ok", "warn", "bad", "off"), name)
			self.assertTrue(data[name]["headline"], name)

	def test_the_agent_is_off_only_with_the_integration(self):
		frappe.db.set_single_value("Periskope Settings", {"enabled": 0, "enable_auto_reply": 1})
		data = flow.get_flow()
		self.assertEqual(
			(data["ai"]["status"], data["waiting"]["status"], data["autoreply"]["status"]), ("off",) * 3
		)

	def test_the_reminder_ladder_is_the_fixed_one(self):
		levels = flow.get_flow()["sla"]["levels"]
		self.assertEqual(len(levels), len(reminders.default_levels()))
		self.assertEqual(levels[0]["action"], "Notify Rep")

	def test_a_tag_can_be_switched_off_but_not_the_default(self):
		flow.set_tag_active("sample_request", False)
		self.assertEqual(frappe.db.get_value("Periskope Tag", "sample_request", "active"), 0)
		flow.set_tag_active("sample_request", True)
		self.assertEqual(frappe.db.get_value("Periskope Tag", "sample_request", "active"), 1)
		with self.assertRaises(frappe.ValidationError):
			flow.set_tag_active("general", False)

	def test_the_figures_carry_the_settings_the_panels_edit(self):
		values = flow.get_flow()["settings"]
		for name in flow.SETTING_FIELDS:
			self.assertIn(name, values)

	def test_a_setting_is_saved_by_the_dialog_and_unknown_fields_are_refused(self):
		flow.set_setting("reply_delay_minutes", 20)
		self.assertEqual(frappe.db.get_single_value("Periskope Settings", "reply_delay_minutes"), 20)
		for name in ("api_key", "base_url", "webhook_signing_secret", "test_mode"):
			with self.assertRaises(frappe.ValidationError, msg=name):
				flow.set_setting(name, "x")

	def test_the_settings_own_checks_still_apply_to_the_dialog(self):
		flow.set_setting("working_hours_start", "09:00:00")
		with self.assertRaises(frappe.ValidationError):
			flow.set_setting("working_hours_end", "09:05:00")
		with self.assertRaises(frappe.ValidationError):
			flow.set_setting("alert_emails", "not-an-email")

	def test_only_managers_can_use_the_flow_api(self):
		original = frappe.session.user
		self.addCleanup(frappe.set_user, original)
		frappe.set_user("Guest")
		# only_for() lets everything through while the test runner is on; switch that off here.
		was_testing = frappe.flags.in_test
		frappe.flags.in_test = False
		self.addCleanup(setattr, frappe.flags, "in_test", was_testing)
		for call in (
			settings_api.get_flow,
			lambda: settings_api.set_tag_active("general", 1),
			lambda: settings_api.set_flow_setting("reply_delay_minutes", 1),
		):
			with self.assertRaises(frappe.PermissionError):
				call()


class TestSetupSteps(FrappeTestCase):
	"""The four setup steps at the top of the settings, and the gate on auto reply."""

	def setUp(self):
		from periskope_connector.tests.utils import isolate

		isolate(self, "periskope_setup_test")

	def _settings(self, **values):
		doc = frappe.get_doc("Periskope Settings")
		doc.update(values)
		return doc

	def test_each_step_says_what_is_missing(self):
		from periskope_connector import setup_status

		doc = self._settings(enabled=0)
		steps = {s["key"]: s for s in setup_status.get_status(doc)["steps"]}
		self.assertFalse(steps["periskope"]["ok"])
		self.assertIn("Enable", steps["periskope"]["message"])
		doc = self._settings(ai_monthly_budget_tokens=0)
		steps = {s["key"]: s for s in setup_status.get_status(doc)["steps"]}
		self.assertFalse(steps["ai"]["ok"])

	def test_approvers_must_be_active_users_with_an_approver_role(self):
		from periskope_connector import setup_status

		problems = setup_status.approver_problems("nobody@example.com\nAdministrator")
		self.assertEqual(len(problems), 1)
		self.assertIn("nobody@example.com", problems[0])

	def test_auto_reply_cannot_be_switched_on_before_the_setup_is_done(self):
		doc = self._settings(enable_auto_reply=1, ai_monthly_budget_tokens=0)
		with self.assertRaises(frappe.ValidationError):
			doc.validate_assistant()

	def test_the_agent_runs_with_the_integration_and_auto_reply_needs_its_switch(self):
		from periskope_connector.setup_status import agent_on, auto_reply_on

		self.assertTrue(agent_on(SimpleNamespace(enabled=1)))
		self.assertFalse(agent_on(SimpleNamespace(enabled=0)))
		self.assertFalse(auto_reply_on(SimpleNamespace(enabled=0, enable_auto_reply=1)))
		self.assertFalse(auto_reply_on(SimpleNamespace(enabled=1, enable_auto_reply=0)))
		self.assertTrue(auto_reply_on(SimpleNamespace(enabled=1, enable_auto_reply=1)))
