"""The recording of a WhatsApp call (calls.py), for the call's row in the inbox conversation.

Periskope's recording link can carry a token that expires after an hour, so it is asked for when the
call is played, never stored. Anyone who may open the call's chat may play it.
"""

import frappe
from frappe import _

from periskope_connector import access
from periskope_connector.client import request


@frappe.whitelist()
def recording(call: str) -> dict:
	"""{url, duration}: a link to the call's recording (WAV), fresh from Periskope."""
	access.require_inbox_user()
	row = frappe.db.get_value(
		"Periskope Call", call, ["name", "periskope_chat", "has_recording"], as_dict=True
	)
	if not row or not row.periskope_chat or not access.can_see_chat(row.periskope_chat):
		frappe.throw(_("You cannot open this call."), frappe.PermissionError)
	if not row.has_recording:
		frappe.throw(_("This call has no recording."))
	full = request("GET", f"calls/{row.name}") or {}
	rec = full.get("recording") or {}
	if not rec.get("url"):
		frappe.throw(_("Periskope has no recording for this call right now."))
	return {"url": rec["url"], "duration": rec.get("duration_seconds")}
