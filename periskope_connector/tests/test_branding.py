"""Settings > Branding (branding.py): the app's name, logo and colour, and the install manifest that
follows the installed name and icon. Empty settings keep the standard look."""

import json

import frappe
from frappe.tests.utils import FrappeTestCase

from periskope_connector import branding
from periskope_connector.ai import festival
from periskope_connector.api import admin, inbox
from periskope_connector.pwa import ENDPOINT, ServiceWorkerRenderer

FIELDS = ("brand_name", "brand_logo", "brand_color", "app_name", "app_icon", "festival_themes")


class TestBranding(FrappeTestCase):
	def setUp(self):
		self.saved = {f: frappe.db.get_single_value("Periskope Settings", f) for f in FIELDS}

	def tearDown(self):
		self._set(**self.saved)
		frappe.cache().delete_value(festival.cache_key())

	def _set(self, **values):
		for field, value in values.items():
			frappe.db.set_single_value("Periskope Settings", field, value)
		frappe.clear_document_cache("Periskope Settings", "Periskope Settings")

	def _manifest(self):
		renderer = branding.ManifestRenderer(path=branding.MANIFEST_ENDPOINT)
		self.assertTrue(renderer.can_render())
		response = renderer.render()
		self.assertEqual(response.mimetype, "application/manifest+json")
		return json.loads(response.get_data(as_text=True))

	def test_empty_settings_keep_the_standard_look(self):
		self._set(brand_name="", brand_logo="", brand_color="", app_name="", app_icon="")
		brand = inbox.boot()["brand"]
		self.assertEqual((brand["name"], brand["logo"], brand["color"]), ("WhatsApp", branding.LOGO, None))
		manifest = self._manifest()
		self.assertEqual(manifest["name"], branding.APP_NAME)
		self.assertIn("maskable", {i["purpose"] for i in manifest["icons"]})

	def test_the_brand_reaches_the_app_and_the_installed_name_and_icon(self):
		self._set(
			brand_name="Pranera Sales",
			brand_logo="/files/logo.png",
			brand_color="#7c3aed",
			app_name="Pranera Chat",
			app_icon="/files/icon.png",
		)
		brand = inbox.boot()["brand"]
		self.assertEqual(
			(brand["name"], brand["logo"], brand["color"]), ("Pranera Sales", "/files/logo.png", "#7c3aed")
		)
		manifest = self._manifest()
		self.assertEqual((manifest["name"], manifest["short_name"]), ("Pranera Chat", "Pranera Chat"))
		self.assertEqual({i["src"] for i in manifest["icons"]}, {"/files/icon.png"})
		self.assertEqual(manifest["scope"], "/whatsapp/")

	def test_the_offline_page_carries_the_name_safely(self):
		self._set(app_name="A `${x}` <b>")
		body = ServiceWorkerRenderer(path=ENDPOINT).render().get_data(as_text=True)
		self.assertIn("Offline · A &#96;&#36;{x}&#96; &lt;b&gt;", body)
		self.assertNotIn("__APP_NAME__", body)

	def test_admins_edit_branding_in_settings(self):
		section = next(s for s in admin.SECTIONS if s["key"] == "branding")
		self.assertEqual(set(section["fields"]), set(FIELDS))
		meta = frappe.get_meta("Periskope Settings")
		self.assertTrue(all(meta.get_field(f) for f in FIELDS))

	def test_festival_themes_off_hides_the_festival(self):
		festival.preview("diwali")
		self._set(festival_themes=1)
		self.assertEqual(festival.current()["look"], "diwali")
		self._set(festival_themes=0)
		self.assertIsNone(festival.current())
		self.assertIsNone(inbox.boot()["festival"])
