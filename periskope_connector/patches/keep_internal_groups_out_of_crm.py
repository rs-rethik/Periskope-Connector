"""Team (Internal) groups stay out of the CRM: the timeline comments and AI notes their messages
put on leads and deals are removed, and every group's member list is read so customers' groups are
found by their members. Both run as jobs, so migrate does not wait on Periskope or big deletes."""

import frappe


def execute():
	frappe.enqueue(
		"periskope_connector.groups.remove_internal_groups_from_crm", queue="long", enqueue_after_commit=True
	)
	frappe.enqueue(
		"periskope_connector.groups.map_groups_daily",
		queue="long",
		job_id="periskope_map_groups_daily",
		deduplicate=True,
		enqueue_after_commit=True,
	)
