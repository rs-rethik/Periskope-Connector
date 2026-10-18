"""Grouped counts and newest values. Frappe v16 refuses SQL functions written as field strings
("count(name) as n", "max(creation)"), and v15 does not know v16's dict syntax for them, so these use
the query builder, which both understand."""

import frappe
from frappe.query_builder import DocType
from frappe.query_builder.functions import Count


def counts(doctype: str, by: list[str], filters: dict | None = None) -> list[frappe._dict]:
	"""How many rows match `filters` for each combination of the `by` fields: [{<by...>, n}]."""
	table = DocType(doctype)
	query = (
		frappe.qb.get_query(doctype, fields=by, filters=filters or {})
		.select(Count("*").as_("n"))
		.groupby(*[table[f] for f in by])
	)
	return query.run(as_dict=True)


def latest(doctype: str, field: str, filters: dict | None = None):
	"""The highest value of `field` among the rows matching `filters`, or None."""
	rows = frappe.get_all(
		doctype,
		filters={**(filters or {}), field: ["is", "set"]},
		fields=[field],
		order_by=f"{field} desc",
		limit=1,
		pluck=field,
	)
	return rows[0] if rows else None
