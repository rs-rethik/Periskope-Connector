"""Groups taken for team groups get another look with every member, also those who never wrote, read
from Periskope (linking.group_member_numbers). It runs as a job, so migrate does not wait on Periskope.
Groups already mapped, by hand or otherwise, are not touched, so running it again changes nothing."""

import frappe


def execute():
	frappe.enqueue("periskope_connector.linking.map_groups_daily", queue="long", enqueue_after_commit=True)
