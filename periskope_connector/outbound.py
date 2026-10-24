"""The one way the app sends a WhatsApp message: the auto reply and a rep's reply from the CRM both
come here. Every attempt is a Periskope Agent Action row: Live (sent, with Periskope's id), Dry Run
(test mode, not an allowed chat: what would have been sent) or Blocked (a group in test mode)."""

import frappe

from periskope_connector import client, numbers, safety
from periskope_connector.api.permission import require_admin


def send(
	chat_id: str,
	text: str,
	phone: str,
	origin: str,
	media: dict | None = None,
	reply_to: str | None = None,
	**values,
) -> dict:
	"""Send `text` (or a file with `text` as its caption) to `chat_id` from the company number
	`phone`. Returns Periskope's reply ({status, unique_id, ...}). Raises safety.SendNotAllowed when
	test mode stops it, and the Periskope error when the send fails; both are logged first."""
	detail = values.pop("detail", None) or {}
	if media:
		detail = {**detail, "file": media.get("filename"), "mimetype": media.get("mimetype")}
	where = {"chat_id": chat_id, "origin": origin, "message_text": text, **values}
	try:
		result = client.send_message(chat_id, text, phone, media=media, reply_to=reply_to)
	except safety.SendNotAllowed as e:
		verb = "Would send" if e.mode == safety.DRY_RUN else "Blocked"
		safety.log("Send Message", e.mode, f"{verb} a WhatsApp message ({origin})", detail=detail, **where)
		raise
	except Exception as e:
		safety.log(
			"Send Message",
			safety.LIVE,
			f"WhatsApp send failed ({origin})",
			error=str(e)[:500],
			detail=detail,
			**where,
		)
		raise
	result = result if isinstance(result, dict) else {}
	safety.log(
		"Send Message",
		safety.LIVE,
		f"Sent a WhatsApp message ({origin})",
		external_id=result.get("unique_id") or "",
		detail={**detail, "periskope": result},
		**where,
	)
	return result


def send_test(chat_id: str, text: str = "", phone: str = "") -> dict:
	"""One real message to check the connection, from the console:
	bench --site <site> execute periskope_connector.outbound.send_test --args '["9163...@c.us"]'
	Only allowed chats get it while test mode is on."""
	require_admin()
	phone = phone or numbers.first_ready()
	text = text or "Periskope connector test message. Please ignore."
	result = send(chat_id, text, phone, origin="Manual Test")
	frappe.db.commit()
	return result


def match_sent(chat_id: str, data: dict) -> str:
	"""For a message from our own number arriving through the webhook: "Answer" or "Holding" when it
	is the auto reply's own send, so it is not taken for a rep's reply. "" for anything else.

	Periskope's copy carries the unique_id it returned on send (its message_id is
	"true_<chat>_<unique_id>"), so the send is found exactly; the text match is the fallback."""
	from periskope_connector.autoreply import match_sent as match_by_text

	# "true_<chat>_<id>", with "_<participant>" after it in a group: the id is the third part.
	parts = (data.get("message_id") or "").split("_")
	unique_id = data.get("unique_id") or (parts[2] if len(parts) >= 3 else "")
	if unique_id:
		detail = frappe.db.get_value(
			"Periskope Agent Action",
			{"action": "Send Message", "mode": safety.LIVE, "external_id": unique_id},
			"detail",
		)
		if detail:
			auto_reply = (frappe.parse_json(detail) or {}).get("auto_reply")
			status = auto_reply and frappe.db.get_value("Periskope Auto Reply", auto_reply, "status")
			return {"Sent": "Answer", "Holding Sent": "Holding"}.get(status, "")
	return match_by_text(chat_id, data.get("body"))
