"""Retention: clear stored message content after the configured periods.

`raw_payload` (the webhook body) goes first — it is pure operational debug data. Message text can
be kept forever by default, because the conversation history is the point of the system; an
operator who needs a stricter policy sets `body_retention_days`. Links, AI tags and summaries
survive either way. Runs monthly as a scheduler job, in batches, so the table is never locked
in one huge transaction.
"""

import frappe
from frappe.utils import add_days, now_datetime

BATCH = 500


def clear(doctype: str, field: str, days: int) -> int:
	"""Clear `field` on rows older than `days`. Returns how many rows were changed."""
	if not days or days <= 0:
		return 0
	cutoff = add_days(now_datetime(), -days)
	total = 0
	while True:
		names = frappe.get_all(
			doctype,
			filters={"creation": ["<", cutoff], field: ["is", "set"]},
			pluck="name",
			limit_page_length=BATCH,
			order_by="creation asc",
		)
		if not names:
			return total
		frappe.db.set_value(doctype, {"name": ["in", names]}, field, "")
		total += len(names)
		frappe.db.commit()


def run() -> dict:
	"""Scheduler, monthly: apply the retention settings."""
	settings = frappe.get_cached_doc("Periskope Settings")
	return {
		"raw_payload_rows": clear(
			"Periskope Message", "raw_payload", cint(settings.raw_payload_retention_days)
		),
		"body_rows": clear("Periskope Message", "body", cint(settings.body_retention_days)),
	}


def cint(value) -> int:
	try:
		return int(value or 0)
	except (TypeError, ValueError):
		return 0
