"""Number cards for the Periskope workspace that follow the tags: each counts the last week's
messages whose tag is in a dashboard group (Enquiry or Issue), set on the tag itself."""

import frappe
from frappe import _
from frappe.utils import add_days, now_datetime

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
