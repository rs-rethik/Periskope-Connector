"""Retention: clear the raw webhook body of messages older than RAW_PAYLOAD_DAYS.

`raw_payload` is pure operational debug data. Message text is kept, because the conversation history
is the point of the system. Runs monthly as a scheduler job, in batches, so the table is never
locked in one huge transaction.
"""

import frappe
from frappe.utils import add_days, now_datetime

BATCH = 500
RAW_PAYLOAD_DAYS = 90


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
	"""Scheduler, monthly: clear the raw payloads older than RAW_PAYLOAD_DAYS."""
	return {"raw_payload_rows": clear("Periskope Message", "raw_payload", RAW_PAYLOAD_DAYS)}
