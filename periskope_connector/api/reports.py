"""The Reports page of the inbox: the app's reports (the Assistant's list), run as the signed-in user
with a date range, shown as a table and downloaded as CSV, without opening Desk. A report opens for
the users its roles allow, as in Desk."""

import frappe
from frappe import _

from periskope_connector import access
from periskope_connector.api import help_bot

MAX_ROWS = 2000


@frappe.whitelist(methods=["GET", "POST"])
def reports() -> list[dict]:
	"""The reports the user may open: {name, title, description, has_period, days}."""
	access.require_inbox_user()
	return [
		{
			"name": name,
			"title": name.replace("Periskope ", ""),
			"description": help_bot.REPORTS[name][0],
			"has_period": help_bot.REPORTS[name][1] is not None,
			"days": help_bot.REPORTS[name][1],
		}
		for name in help_bot.allowed_reports()
	]


@frappe.whitelist(methods=["GET", "POST"])
def run(report: str, from_date: str = "", to_date: str = "") -> dict:
	"""{columns, rows, from_date, to_date, more}: the report run as the user, at most MAX_ROWS rows."""
	access.require_inbox_user()
	if report not in help_bot.REPORTS or report not in help_bot.allowed_reports():
		frappe.throw(_("You cannot open this report."), frappe.PermissionError)
	from_date, to_date = help_bot._dates(report, from_date, to_date)
	filters = {k: v for k, v in {"from_date": from_date, "to_date": to_date}.items() if v}
	result = frappe.get_doc("Report", report).execute_script_report(filters)
	columns = [
		{"fieldname": c.get("fieldname"), "label": c.get("label"), "fieldtype": c.get("fieldtype")}
		for c in (result[0] or [])
		if isinstance(c, dict)
	]
	rows = result[1] or []
	return {
		"columns": columns,
		"rows": rows[:MAX_ROWS],
		"more": len(rows) > MAX_ROWS,
		"from_date": from_date,
		"to_date": to_date,
	}
