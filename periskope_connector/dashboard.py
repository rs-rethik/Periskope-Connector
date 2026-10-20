"""What the Periskope workspace shows that follows the tags: the Enquiry and Issue number cards (the
last week's messages whose tag is in that dashboard group, set on the tag itself) and the Messages by
Tag block (install.DASHBOARD_BLOCK)."""

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

from periskope_connector import query
from periskope_connector.api.permission import require_manager

DAYS = 7


def _count_group(group: str) -> dict:
	names = frappe.get_all("Periskope Tag", filters={"dashboard_group": group}, pluck="name")
	since = add_days(now_datetime(), -DAYS)
	value = (
		frappe.db.count(
			"Periskope Message",
			{"ai_category": ["in", names], "message_timestamp": [">=", since], "direction": "Incoming"},
		)
		if names
		else 0
	)
	return {
		"value": value,
		"fieldtype": "Int",
		"route": ["List", "Periskope Message"],
		"route_options": {"ai_category": ["in", names], "direction": "Incoming"},
	}


@frappe.whitelist()
def enquiries() -> dict:
	"""Incoming messages of the last 7 days whose tag is in the Enquiry group."""
	require_manager()
	return _count_group("Enquiry")


@frappe.whitelist()
def issues() -> dict:
	"""Incoming messages of the last 7 days whose tag is in the Issue group."""
	require_manager()
	return _count_group("Issue")


TAG_DAYS = 30


@frappe.whitelist()
def tag_counts() -> list[dict]:
	"""The workspace's Messages by Tag block: incoming messages of the last 30 days per tag, most first,
	with every active tag listed (a tag nobody used shows 0)."""
	require_manager()
	since = add_days(now_datetime(), -TAG_DAYS)
	counts = {
		r.ai_category: r.n
		for r in query.counts(
			"Periskope Message",
			["ai_category"],
			{"ai_category": ["is", "set"], "message_timestamp": [">=", since], "direction": "Incoming"},
		)
	}
	tags = frappe.get_all("Periskope Tag", filters={"active": 1}, pluck="name")
	rows = [{"tag": t, "label": t.replace("_", " "), "count": counts.get(t, 0)} for t in tags]
	return sorted(rows, key=lambda r: (-r["count"], r["tag"]))
