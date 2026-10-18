"""Writes the app makes by itself, not on behalf of a user.

The documents it fills in (webhook logs, messages, reply reminders) are read-only for every
role on purpose, because nobody edits them by hand. So no role could be given a write
permission, and the code that writes them runs with no suitable user anyway: the webhook is a
guest call, and a queued job runs as whoever queued it (Guest, for the webhook).

Permissions are therefore checked where a caller enters, not on each internal write:
- the webhook is authenticated by its HMAC signature,
- every whitelisted method starts with a role check (api/permission.py),
- the functions that use these helpers are not whitelisted, so they cannot be called over HTTP.

Only use these helpers for documents the app generates itself. For a document a user is
meant to edit, or one built from user input, use a normal insert / save so permissions apply.
"""

import frappe
from frappe.model.document import Document


def _without_permission_checks(doc: Document, action):
	"""Run insert / save with permission checks off, and turn them back on afterwards so the
	bypass does not stay on the object for whoever uses it next."""
	previous = doc.flags.ignore_permissions
	try:
		return action(ignore_permissions=True)
	finally:
		doc.flags.ignore_permissions = previous


def insert_as_system(doc: dict | Document) -> Document:
	if isinstance(doc, dict):
		doc = frappe.get_doc(doc)
	_without_permission_checks(doc, doc.insert)
	return doc


def save_as_system(doc: Document) -> Document:
	_without_permission_checks(doc, doc.save)
	return doc
