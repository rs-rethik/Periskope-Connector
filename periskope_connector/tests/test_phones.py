"""Connect / re-link a Periskope number by its QR code from the app (api/phones.py): only the QR
image is passed on, the number's state is kept in step, re-link logs WhatsApp out, Meta numbers and
non-admins are refused. Periskope is never called for real."""

from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import client
from periskope_connector.api import phones
from periskope_connector.api import settings as settings_api
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.utils import OTHER_APP_DOCTYPES, isolate, make_sales_manager, settings

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = OTHER_APP_DOCTYPES

NUMBER = "919600077777"
META = "919600088888"
PNG = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAAB"


def _number(name, source="Periskope", **values):
	if not frappe.db.exists("WhatsApp Number", name):
		frappe.get_doc({"doctype": "WhatsApp Number", "number": name, **values}).insert(
			ignore_permissions=True
		)
	frappe.db.set_value("WhatsApp Number", name, {"source": source, **values})
	return name


class PhonesCase(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_phones_test")
		settings(enabled=1, base_url="https://api.periskope.test/v1")
		_number(NUMBER, periskope_phone_id="phone-1", is_ready=1, wa_state="CONNECTED")
		_number(META, source="Meta")
		self.calls = []
		self.state = "UNPAIRED"
		self.qr_html = f'<html><body><img src="{PNG}" onerror="alert(1)"></body></html>'

		self.all = [{"phone_id": "phone-1", "org_phone": f"{NUMBER}@c.us", "first_connected_at": "x"}]
		self.org_phone = None

		def fake(method, path, phone=None, text=False, missing_ok=False, **kwargs):
			self.calls.append((method, path, phone))
			if path == "phones":
				return {
					"wa_state": self.state,
					"is_ready": self.state == "CONNECTED",
					"org_phone": self.org_phone,
				}
			if path == "phones/qr":
				return self.qr_html
			if path == "phones/all":
				return self.all
			if path == "phones/create":
				return {"phone_id": "phone-new", "org_phone": None, "wa_state": None}
			return {}

		for target in (phones, settings_api):
			p = patch.object(target, "request", side_effect=fake)
			p.start()
			self.addCleanup(p.stop)
		r = patch.object(phones, "_refresh_numbers")
		self.refresh = r.start()
		self.addCleanup(r.stop)
		self.addCleanup(frappe.set_user, "Administrator")


class TestLink(PhonesCase):
	def test_only_the_qr_image_is_passed_on(self):
		out = phones.qr(NUMBER)
		self.assertEqual(out, {"state": "UNPAIRED", "connected": False, "qr": PNG, "number": NUMBER})
		self.assertIn(("GET", "phones/qr", "phone-1"), self.calls)
		row = frappe.db.get_value("WhatsApp Number", NUMBER, ["wa_state", "is_ready"], as_dict=True)
		self.assertEqual((row.wa_state, row.is_ready), ("UNPAIRED", 0))

	def test_no_code_yet_and_no_html(self):
		self.qr_html = None  # Periskope's 404: no code right now
		self.assertIsNone(phones.qr(NUMBER)["qr"])
		self.qr_html = "<p>QR not generated</p>"
		self.assertIsNone(phones.qr(NUMBER)["qr"])

	def test_once_connected_no_code_and_the_numbers_are_read_again(self):
		self.state = "CONNECTED"
		out = phones.qr(NUMBER)
		self.assertEqual(out, {"state": "CONNECTED", "connected": True, "qr": None, "number": NUMBER})
		self.assertNotIn("phones/qr", [c[1] for c in self.calls])
		self.refresh.assert_called_once()
		self.assertEqual(frappe.db.get_value("WhatsApp Number", NUMBER, "is_ready"), 1)

	def test_relink_logs_out_and_waits_for_a_scan(self):
		out = phones.relink(NUMBER)
		self.assertEqual(out, {"state": "UNPAIRED", "connected": False})
		self.assertIn(("POST", "phones/reset", "phone-1"), self.calls)
		self.assertTrue(frappe.db.get_value("WhatsApp Number", NUMBER, "disconnected_since"))

	def test_restart(self):
		phones.restart(NUMBER)
		self.assertIn(("POST", "phones/restart", "phone-1"), self.calls)

	def test_the_number_is_the_header_without_a_phone_id(self):
		frappe.db.set_value("WhatsApp Number", NUMBER, "periskope_phone_id", None)
		phones.state(NUMBER)
		self.assertIn(("GET", "phones", NUMBER), self.calls)


class TestAdd(PhonesCase):
	def test_a_new_phone_is_added_and_its_code_shown_by_its_id(self):
		self.assertEqual(phones.add(), {"phone_id": "phone-new", "reused": False})
		self.assertIn(("POST", "phones/create", None), self.calls)
		out = phones.qr(phone_id="phone-new")
		self.assertEqual(out["qr"], PNG)
		self.assertIn(("GET", "phones/qr", "phone-new"), self.calls)

	def test_a_phone_added_before_and_never_scanned_is_used_again(self):
		self.all.append({"phone_id": "phone-left", "org_phone": None, "first_connected_at": None})
		self.assertEqual(phones.add(), {"phone_id": "phone-left", "reused": True})
		self.assertNotIn("phones/create", [c[1] for c in self.calls])

	def test_once_scanned_the_numbers_are_synced_and_the_number_returned(self):
		self.state, self.org_phone = "CONNECTED", "919600066666@c.us"
		out = phones.qr(phone_id="phone-new")
		self.assertEqual((out["connected"], out["number"]), (True, "919600066666"))
		self.refresh.assert_called_once()

	def test_periskope_adding_nothing_is_an_error(self):
		with patch.object(phones, "request", return_value={}):
			with self.assertRaises(frappe.ValidationError):
				phones.add()

	def test_a_bad_phone_id_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			phones.qr(phone_id="../orgs")
		self.assertEqual(self.calls, [])


class TestRefused(PhonesCase):
	def test_meta_numbers_are_linked_in_meta(self):
		for method in (phones.state, phones.qr, phones.relink, phones.restart):
			with self.assertRaises(frappe.ValidationError):
				method(META)
		self.assertEqual(self.calls, [])

	def test_admins_only(self):
		frappe.set_user(make_sales_manager(_user("pkt.phones.manager@example.com", "Sales Manager")))
		for method in (phones.state, phones.qr, phones.relink, phones.restart):
			with self.assertRaises(frappe.PermissionError):
				method(NUMBER)
		with self.assertRaises(frappe.PermissionError):
			phones.add()
		self.assertEqual(self.calls, [])


class TestClient(FrappeTestCase):
	def setUp(self):
		isolate(self, "periskope_phones_client_test")
		settings(enabled=1, base_url="https://api.periskope.test/v1")
		p = patch.object(frappe.utils.password, "get_decrypted_password", lambda *a, **k: "key")
		p.start()
		self.addCleanup(p.stop)

	def _answer(self, status, body):
		response = SimpleNamespace(status_code=status, ok=status < 400, text=body, json=lambda: {})
		return patch("periskope_connector.client.requests.request", return_value=response)

	def test_text_and_missing_ok(self):
		with self._answer(200, "<img>"):
			self.assertEqual(client.request("GET", "phones/qr", phone="x", text=True), "<img>")
		with self._answer(404, "no qr"):
			self.assertIsNone(client.request("GET", "phones/qr", phone="x", missing_ok=True))
			with self.assertRaises(frappe.ValidationError):
				client.request("GET", "phones/qr", phone="x")


class TestBootTestMode(FrappeTestCase):
	def test_the_app_is_told_when_test_mode_is_on(self):
		from periskope_connector.api import inbox

		isolate(self, "periskope_boot_test_mode")
		settings(test_mode=1)
		self.assertTrue(inbox.boot()["test_mode"])
		settings(test_mode=0)
		self.assertFalse(inbox.boot()["test_mode"])


class TestQueue(PhonesCase):
	def test_the_send_queue_is_read_and_cleared_and_stuck_replies_marked_failed(self):
		self.queue = {"is_running": False, "active": 1, "pending": 3, "completed": 9, "failed": 2}
		with patch.object(phones, "request", return_value=self.queue) as req:
			out = phones.queue(NUMBER)
		self.assertEqual(req.call_args.args[:2], ("GET", "message/queues/health"))
		self.assertEqual((out["running"], out["pending"], out["failed"]), (False, 3, 2))

		stuck = frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": f"local_{frappe.generate_hash(length=12)}",
				"direction": "Outgoing",
				"message_type": "text",
				"body": "hello",
				"chat_id": "919600099999@c.us",
				"whatsapp_number": NUMBER,
				"send_status": "Queued",
			}
		).insert(ignore_permissions=True)
		# WhatsApp has this one (an ack came back) though Periskope's copy has not arrived: not failed
		delivered = frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": f"local_{frappe.generate_hash(length=12)}",
				"direction": "Outgoing",
				"message_type": "text",
				"body": "delivered",
				"chat_id": "919600099999@c.us",
				"whatsapp_number": NUMBER,
				"send_status": "Queued",
				"ack": "2",
			}
		).insert(ignore_permissions=True)
		with patch.object(phones, "request", return_value=self.queue) as req:
			out = phones.purge_queue(NUMBER)
		self.assertEqual(req.call_args_list[0].args[:2], ("POST", "message/queues/purge"))
		self.assertEqual(out["cancelled"], 1)
		self.assertEqual(frappe.db.get_value("Periskope Message", stuck.name, "send_status"), "Failed")
		self.assertEqual(frappe.db.get_value("Periskope Message", delivered.name, "send_status"), "Queued")

	def test_admins_only(self):
		frappe.set_user(make_sales_manager(_user("pkt.queue.manager@example.com", "Sales Manager")))
		for method in (phones.queue, phones.purge_queue):
			with self.assertRaises(frappe.PermissionError):
				method(NUMBER)
