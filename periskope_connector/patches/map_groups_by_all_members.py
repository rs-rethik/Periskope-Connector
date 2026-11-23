"""Groups taken for team groups get another look with every member, also those who never wrote, read
from Periskope (groups.map_groups). It runs as a job, so migrate does not wait on Periskope.
Groups already mapped, by hand or otherwise, are not touched, so running it again changes nothing."""

import frappe


def execute():
	frappe.enqueue(
		"periskope_connector.groups.map_groups_daily",
		queue="long",
		job_id="periskope_map_groups_daily",
		deduplicate=True,
		enqueue_after_commit=True,
	)
