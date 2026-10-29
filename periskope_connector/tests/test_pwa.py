"""The inbox is installable: its service worker is served inside /whatsapp/ (pwa.py) and the
manifest and icons it names exist."""

import json
import os

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import __version__
from periskope_connector.pwa import ENDPOINT, ServiceWorkerRenderer


class TestInstallable(FrappeTestCase):
	def test_the_service_worker_is_served_for_the_whatsapp_scope(self):
		renderer = ServiceWorkerRenderer(path=ENDPOINT)
		self.assertTrue(renderer.can_render())
		self.assertFalse(ServiceWorkerRenderer(path="whatsapp").can_render())
		response = renderer.render()
		self.assertEqual(response.mimetype, "text/javascript")
		self.assertEqual(response.headers["Service-Worker-Allowed"], "/whatsapp/")
		body = response.get_data(as_text=True)
		self.assertIn(f'const VERSION = "{__version__}"', body)
		self.assertNotIn("__VERSION__", body)

	def test_the_manifest_starts_in_the_app_and_its_icons_exist(self):
		public = frappe.get_app_path("periskope_connector", "public")
		with open(os.path.join(public, "pwa", "manifest.json")) as f:
			manifest = json.load(f)
		self.assertEqual((manifest["scope"], manifest["display"]), ("/whatsapp/", "standalone"))
		self.assertTrue(manifest["start_url"].startswith(manifest["scope"]))
		sizes = {i["sizes"] for i in manifest["icons"]}
		self.assertTrue({"192x192", "512x512"} <= sizes)
		self.assertIn("maskable", {i["purpose"] for i in manifest["icons"]})
		for icon in manifest["icons"]:
			path = icon["src"].replace("/assets/periskope_connector/", "")
			self.assertTrue(os.path.isfile(os.path.join(public, path)), icon["src"])
