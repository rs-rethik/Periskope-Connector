import frappe
from frappe.utils import flt

from periskope_connector.reminders import default_levels


def execute():
	"""Turn the old L1/L2/L3 hours and the High-severity minutes into SLA Level rows, so the ladder
	behaves exactly as before until the team edits it. Skipped when rows already exist."""
	settings = frappe.get_single("Periskope Settings")
	if settings.get("sla_levels"):
		return
	for row in default_levels(
		flt(settings.sla_l1_hours) or 1.0,
		flt(settings.sla_l2_hours) or 3.0,
		flt(settings.sla_l3_hours) or 8.0,
		flt(settings.high_severity_minutes) or 30.0,
	):
		settings.append(
			"sla_levels",
			{
				"wait_minutes": row.wait_minutes,
				"min_severity": row.min_severity,
				"action": row.action,
				"recipients": row.recipients,
			},
		)
	settings.flags.ignore_permissions = True
	settings.save()
