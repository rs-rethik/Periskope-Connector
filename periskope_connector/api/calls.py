"""The recording of a WhatsApp call (calls.py), for the call's row in the inbox conversation.

A copy is kept on the site (calls.keep_recording) and played from here; until it is, a fresh link
is asked from Periskope (its link can expire after an hour). Anyone who may open the call's chat may
play it.
"""

import frappe
from frappe import _

from periskope_connector import access
from periskope_connector.client import request


@frappe.whitelist()
def recording(call: str) -> dict:
	"""{url, duration}: a link to the call's recording (WAV), fresh from Periskope."""
	access.require_inbox_user()
	row = _call(call)
	if not row.has_recording:
		frappe.throw(_("This call has no recording."))
	if row.recording_file:
		return {
			"url": f"/api/method/periskope_connector.api.calls.recording_file?call={row.name}",
			"duration": row.recording_duration,
		}
	full = request("GET", f"calls/{row.name}") or {}
	rec = full.get("recording") or {}
	if not rec.get("url"):
		frappe.throw(_("Periskope has no recording for this call right now."))
	return {"url": rec["url"], "duration": rec.get("duration_seconds")}


def _call(call: str) -> frappe._dict:
	access.require_inbox_user()
	row = frappe.db.get_value(
		"Periskope Call",
		call,
		["name", "periskope_chat", "has_recording", "recording_file", "recording_duration"],
		as_dict=True,
	)
	if not row or not row.periskope_chat or not access.can_see_chat(row.periskope_chat):
		frappe.throw(_("You cannot open this call."), frappe.PermissionError)
	return row


@frappe.whitelist(methods=["GET"])
def recording_file(call: str):
	"""The kept copy of the recording, for the player."""
	access.require_inbox_user()
	row = _call(call)
	if not row.recording_file:
		raise frappe.DoesNotExistError
	from periskope_connector import attachments

	frappe.local.response.filename = f"call-{row.name}.wav"
	frappe.local.response.filecontent = attachments._read(row.recording_file)
	frappe.local.response.type = "download"
	frappe.local.response.content_type = "audio/wav"
	frappe.local.response.display_content_as = "inline"
