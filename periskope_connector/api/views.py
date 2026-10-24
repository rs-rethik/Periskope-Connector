"""Saved views of the WhatsApp inbox's list: filters, columns, sort and grouping, kept per user, and
shared with everyone when a manager saves them so."""

import json

import frappe
from frappe import _
from frappe.utils import cint

from periskope_connector import access

FIELDS = ["name", "label", "is_shared", "filters", "columns", "sort_by", "sort_order", "group_by", "owner"]


def _out(row) -> dict:
	row = dict(row)
	for key in ("filters", "columns"):
		row[key] = json.loads(row.get(key) or ("{}" if key == "filters" else "[]"))
	row["mine"] = row["owner"] == frappe.session.user
	return row


@frappe.whitelist()
def list_views() -> list[dict]:
	"""The user's own views and the shared ones."""
	access.require_inbox_user()
	rows = frappe.get_all(
		"Periskope View",
		or_filters={"owner": frappe.session.user, "is_shared": 1},
		fields=FIELDS,
		order_by="label asc",
		limit=200,
	)
	return [_out(r) for r in rows]


@frappe.whitelist(methods=["POST"])
def save_view(
	label: str,
	filters: str | dict | None = None,
	columns: str | list | None = None,
	sort_by: str = "last_message_at",
	sort_order: str = "desc",
	group_by: str = "",
	is_shared: int = 0,
	name: str | None = None,
) -> dict:
	access.require_inbox_user()
	if not (label or "").strip():
		frappe.throw(_("Give the view a name."))
	values = {
		"label": label.strip(),
		"filters": filters if isinstance(filters, str) else json.dumps(filters or {}),
		"columns": columns if isinstance(columns, str) else json.dumps(columns or []),
		"sort_by": sort_by,
		"sort_order": "asc" if sort_order == "asc" else "desc",
		"group_by": group_by or "",
		"is_shared": 1 if cint(is_shared) and access.is_manager() else 0,
	}
	if name:
		doc = frappe.get_doc("Periskope View", name)
		if doc.owner != frappe.session.user and not access.is_manager():
			frappe.throw(_("You can only change your own views."), frappe.PermissionError)
		doc.update(values)
		doc.save(ignore_permissions=True)
	else:
		doc = frappe.get_doc({"doctype": "Periskope View", **values}).insert(ignore_permissions=True)
	return _out({f: doc.get(f) for f in FIELDS})


@frappe.whitelist(methods=["POST"])
def delete_view(name: str) -> None:
	access.require_inbox_user()
	owner = frappe.db.get_value("Periskope View", name, "owner")
	if owner != frappe.session.user and not access.is_manager():
		frappe.throw(_("You can only delete your own views."), frappe.PermissionError)
	frappe.delete_doc("Periskope View", name, ignore_permissions=True)
