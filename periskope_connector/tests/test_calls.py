"""WhatsApp calls (calls.py, api/calls.py): saved from Periskope's calls API into their chats, shown in
the conversation, a bell for a missed call, the CRM's Calls tab for a linked lead (test mode as for
every CRM write), and the recording asked for fresh. Periskope is never called for real."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from periskope_connector import calls
from periskope_connector.api import calls as calls_api
from periskope_connector.api import inbox
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

NUMBER = "919600071111"
CUSTOMER = "919600072222"
STRANGER = "919600073333"
REP = "pkt.calls.rep@example.com"


def _iso(minutes_ago: int = 5) -> str:
	"""A time as Periskope sends it (UTC ISO)."""
	from periskope_connector.calls import _utc

	return _utc(add_to_date(now_datetime(), minutes=-minutes_ago)).replace(":00Z", ":00.000Z")


def _call(call_id="CALL1", who=CUSTOMER, result="missed", direction="incoming", **extra):
	return {
		"call_id": call_id,
		"org_phone": f"{NUMBER}@c.us",
		"chat_id": f"{who}@c.us",
		"chat_name": "Test Caller",
		"direction": direction,
		"result": result,
		"handled_via": "phone",
		"timestamp": _iso(extra.pop("minutes_ago", 5)),
		"duration_seconds": 0,
		**extra,
	}


class CallsCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_calls_test")
		settings(
			enabled=1, test_mode=1, test_allowed_chats=CUSTOMER, base_url="https://api.periskope.test/v1"
		)
		_user(REP, "Sales User")
		if not frappe.db.exists("WhatsApp Number", NUMBER):
			frappe.get_doc({"doctype": "WhatsApp Number", "number": NUMBER}).insert(ignore_permissions=True)
		frappe.db.set_value("WhatsApp Number", NUMBER, {"source": "Periskope", "default_assignee": REP})
		self.full = {}
		self.pages = []

		def fake(method, path, phone=None, params=None, **kwargs):
			if path == "calls":
				return {"calls": self.pages.pop(0) if self.pages else []}
			if path.startswith("calls/"):
				return self.full
			return {}

		for target in (calls, calls_api):
			p = patch.object(target, "request", side_effect=fake)
			p.start()
			self.addCleanup(p.stop)

	def _bells(self):
		return frappe.db.count(
			"Notification Log", {"for_user": REP, "subject": ["like", "Missed WhatsApp call%"]}
		)


class TestSave(CallsCase):
	def test_a_missed_call_from_a_new_number_starts_its_chat_and_rings_the_rep(self):
		before = self._bells()
		calls.save(_call(who=STRANGER))
		row = frappe.get_doc("Periskope Call", "CALL1")
		self.assertTrue(row.periskope_chat)
		self.assertEqual(
			frappe.db.get_value("Periskope Chat", row.periskope_chat, "chat_id"), f"{STRANGER}@c.us"
		)
		self.assertEqual(self._bells(), before + 1)
		calls.save(_call(who=STRANGER))  # the next sync reads it again: one row, one bell
		self.assertEqual(frappe.db.count("Periskope Call", {"call_id": "CALL1"}), 1)
		self.assertEqual(self._bells(), before + 1)

	def test_an_old_missed_call_and_an_answered_one_ring_nobody(self):
		before = self._bells()
		calls.save(_call("OLD", minutes_ago=60 * 24))
		calls.save(_call("OK", result="connected", duration_seconds=95))
		self.assertEqual(self._bells(), before)
		self.assertEqual(frappe.db.get_value("Periskope Call", "OK", "duration"), 95)

	def test_groups_and_unresolved_callers_get_no_chat(self):
		calls.save(_call("LID", chat_id="12345@lid"))
		self.assertFalse(frappe.db.get_value("Periskope Call", "LID", "periskope_chat"))

	def test_the_transcript_is_read_once_it_is_done(self):
		self.full = {
			"transcript": {
				"status": "done",
				"utterances": [{"speaker": "Test Caller", "text": "Price of 34s?"}],
			},
			"summary": {"text": "Asked the price."},
		}
		calls.save(
			_call(
				"REC",
				result="connected",
				handled_via="periskope",
				transcript={"status": "done"},
				recording={"url": "https://x/rec.wav", "duration_seconds": 40},
			)
		)
		row = frappe.get_doc("Periskope Call", "REC")
		self.assertEqual(row.transcript, "Test Caller: Price of 34s?")
		self.assertEqual(row.summary, "Asked the price.")
		self.assertEqual((row.has_recording, row.recording_duration), (1, 40))

	def test_the_call_shows_in_the_conversation(self):
		calls.save(_call("SHOWN", result="connected", answered_by="Administrator", duration_seconds=30))
		chat = frappe.db.get_value("Periskope Call", "SHOWN", "periskope_chat")
		page = inbox.get_messages(chat)
		self.assertIn("SHOWN", [c.name for c in page["calls"]])


class TestSync(CallsCase):
	def test_it_reads_page_by_page_from_the_last_call(self):
		self.pages = [[_call(f"P{i}", result="connected") for i in range(calls.PAGE)], [_call("LAST")]]
		with patch.object(calls, "request", wraps=calls.request) as spy:
			out = calls.sync()
		self.assertEqual(out["saved"], calls.PAGE + 1)
		offsets = [c.kwargs["params"]["offset"] for c in spy.call_args_list]
		self.assertEqual(offsets, [0, calls.PAGE])
		self.assertTrue(spy.call_args_list[0].kwargs["params"]["start_time"].endswith("Z"))

	def test_nothing_when_switched_off(self):
		settings(enabled=0)
		self.pages = [[_call("OFF")]]
		calls.sync()
		self.assertFalse(frappe.db.exists("Periskope Call", "OFF"))


class TestCRM(CallsCase):
	def setUp(self):
		super().setUp()
		if not frappe.db.exists("DocType", "CRM Call Log"):
			self.skipTest("Frappe CRM is not installed")
		lead = frappe.get_doc({"doctype": "CRM Lead", "first_name": "Caller", "mobile_no": f"+{CUSTOMER}"})
		lead.flags.ignore_mandatory = True
		lead.insert(ignore_permissions=True)
		self.lead = lead.name

	def _linked(self, call_id, who=CUSTOMER, **extra):
		calls.save(_call(call_id, who=who, **extra))
		chat = frappe.db.get_value("Periskope Call", call_id, "periskope_chat")
		frappe.db.set_value("Periskope Chat", chat, "crm_lead", self.lead)
		calls.to_crm(frappe.get_doc("Periskope Call", call_id))

	def test_a_call_goes_to_the_leads_calls_tab_with_its_summary(self):
		self._linked("CRM1", result="connected", duration_seconds=61, summary={"text": "Wants a quote."})
		log = frappe.db.get_value("Periskope Call", "CRM1", "crm_call_log")
		row = frappe.get_doc("CRM Call Log", log)
		self.assertEqual((row.type, row.status, row.duration), ("Incoming", "Completed", 61))
		self.assertEqual((row.reference_doctype, row.reference_docname), ("CRM Lead", self.lead))
		self.assertEqual((row.get("from"), row.to), (f"+{CUSTOMER}", f"+{NUMBER}"))
		self.assertIn("Wants a quote.", frappe.db.get_value("FCRM Note", row.note, "content"))
		calls.to_crm(frappe.get_doc("Periskope Call", "CRM1"))  # unchanged: written once
		self.assertEqual(frappe.db.count("CRM Call Log", {"reference_docname": self.lead}), 1)

	def test_test_mode_keeps_other_customers_out_of_the_crm(self):
		self._linked("CRM2", who=STRANGER, result="missed")
		self.assertFalse(frappe.db.get_value("Periskope Call", "CRM2", "crm_call_log"))


class TestRecording(CallsCase):
	def test_a_fresh_link_for_whoever_may_open_the_chat(self):
		calls.save(_call("PLAY", result="connected", recording={"url": "https://x/old.wav"}))
		self.full = {"recording": {"url": "https://x/fresh.wav", "duration_seconds": 12}}
		self.assertEqual(calls_api.recording("PLAY")["url"], "https://x/fresh.wav")
		frappe.set_user(_user("pkt.calls.other@example.com", "Sales User"))
		self.addCleanup(frappe.set_user, "Administrator")
		with self.assertRaises(frappe.PermissionError):
			calls_api.recording("PLAY")
