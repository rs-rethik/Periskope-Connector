import frappe
from frappe.rate_limiter import rate_limit

from periskope_connector import suggestions


@frappe.whitelist(allow_guest=True, methods=["POST"])
@rate_limit(limit=30, seconds=60)
def decide(token: str, decision: str) -> dict:
	"""The Approve / Reject (or Undo) buttons of the confirmation page behind the emailed links. The token
	is the credential: random, stored only as a hash, valid for one suggestion and one use."""
	if decision not in ("approve", "reject", "undo"):
		return {"ok": False, "message": "Unknown decision"}
	return suggestions.decide_by_token(token, decision)
