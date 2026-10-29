"""Serve the inbox's service worker inside the app's own URL scope.

A service worker controls only the path it is served from (or below) unless the response carries
``Service-Worker-Allowed``. The file lives under ``/assets/periskope_connector/pwa/``, which cannot
control ``/whatsapp/``, so the same file is served at ``/whatsapp/sw.js`` (hooks.py routes it to the
``whatsapp_sw`` endpoint) with that header, which works on ``bench serve`` and behind nginx alike. The
app version goes into the file, so a release clears the old cache."""

import os

import frappe
from frappe.website.page_renderers.base_renderer import BaseRenderer
from werkzeug.wrappers import Response

ENDPOINT = "whatsapp_sw"
SCOPE = "/whatsapp/"


class ServiceWorkerRenderer(BaseRenderer):
	def __init__(self, path=None, http_status_code=None):
		super().__init__(path=path, http_status_code=http_status_code)
		self.file_path = frappe.get_app_path("periskope_connector", "public", "pwa", "sw.js")

	def can_render(self):
		return self.path == ENDPOINT and os.path.isfile(self.file_path)

	def render(self):
		from periskope_connector import __version__, branding

		with open(self.file_path, encoding="utf-8") as f:
			body = f.read().replace("__VERSION__", __version__)
		# the name goes into the offline page, an HTML string inside a JS template literal
		name = frappe.utils.escape_html(branding.app_name())
		for char, entity in (("`", "&#96;"), ("$", "&#36;"), ("\\", "&#92;")):
			name = name.replace(char, entity)
		body = body.replace("__APP_NAME__", name)
		response = Response(body)
		response.mimetype = "text/javascript"
		response.headers["Service-Worker-Allowed"] = SCOPE
		response.headers["Cache-Control"] = "no-cache, must-revalidate"
		return response
