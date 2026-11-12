"""Chats not resolved in time now alert the managers once (sla._resolution_breaches). The open chats
already past their deadline before this change are counted as alerted, so the upgrade does not send
one email per old chat."""

import frappe
from frappe.utils import now_datetime


def execute():
	c = frappe.qb.DocType("Periskope Chat")
	(
		frappe.qb.update(c)
		.set(c.resolution_alerted, 1)
		.where(c.resolution_due.isnotnull() & (c.resolution_due < now_datetime()))
	).run()
