"""The app's branding from Periskope Settings (Settings > Branding): its name, logo and colour, and the
name and icon it gets when installed. Empty settings keep the standard look.

The install manifest is served at /whatsapp/manifest.webmanifest (hooks.py routes it here) so the
installed name and icon follow the settings. Browsers fetch it without the login cookie, so it is open
to guests; it holds nothing but the name and the icon."""

import json

import frappe
from frappe.website.page_renderers.base_renderer import BaseRenderer
from werkzeug.wrappers import Response

NAME = "WhatsApp"
APP_NAME = "Pranera Connect"
LOGO = "/assets/periskope_connector/images/logo.svg"
MANIFEST_ENDPOINT = "whatsapp_manifest"


def brand() -> dict:
	s = frappe.get_cached_doc("Periskope Settings")
	return {
		"name": (s.get("brand_name") or "").strip() or NAME,
		"logo": s.get("brand_logo") or LOGO,
		"color": s.get("brand_color") or None,
		"app_name": app_name(s),
		"app_icon": s.get("app_icon") or None,
	}


def app_name(settings=None) -> str:
	s = settings or frappe.get_cached_doc("Periskope Settings")
	return (s.get("app_name") or "").strip() or APP_NAME


def festival_themes() -> bool:
	return bool(frappe.get_cached_doc("Periskope Settings").get("festival_themes"))


def manifest() -> dict:
	path = frappe.get_app_path("periskope_connector", "public", "pwa", "manifest.json")
	with open(path, encoding="utf-8") as f:
		data = json.load(f)
	s = frappe.get_cached_doc("Periskope Settings")
	data["name"] = data["short_name"] = app_name(s)
	if s.get("app_icon"):
		# the uploaded icon may not be safe to crop to a circle, so it is not offered as maskable
		data["icons"] = [
			{"src": s.app_icon, "sizes": size, "purpose": "any"} for size in ("192x192", "512x512")
		]
	return data


class ManifestRenderer(BaseRenderer):
	def can_render(self):
		return self.path == MANIFEST_ENDPOINT

	def render(self):
		response = Response(json.dumps(manifest(), ensure_ascii=False))
		response.mimetype = "application/manifest+json"
		response.headers["Cache-Control"] = "no-cache, must-revalidate"
		return response
