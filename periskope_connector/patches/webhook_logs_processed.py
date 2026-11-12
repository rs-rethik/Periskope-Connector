"""Webhook logs now say whether they were applied (processed). The logs saved before this change were
handled by the old code, so they count as processed; otherwise the sweep would apply them all again."""

import frappe


def execute():
	log = frappe.qb.DocType("Periskope Webhook Log")
	frappe.qb.update(log).set(log.processed, 1).where(log.processed == 0).run()
