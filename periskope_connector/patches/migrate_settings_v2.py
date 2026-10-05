import frappe
from frappe.utils import cint, flt


def _single(field: str):
	row = frappe.db.sql(
		"select value from `tabSingles` where doctype = 'Periskope Settings' and field = %s", (field,)
	)
	return row[0][0] if row else None


def _set(field: str, value):
	frappe.db.sql("delete from `tabSingles` where doctype = 'Periskope Settings' and field = %s", (field,))
	if value is not None:
		frappe.db.sql(
			"insert into `tabSingles` (doctype, field, value) values ('Periskope Settings', %s, %s)",
			(field, str(value)),
		)


def execute():
	"""v2 upgrade of settings and in-flight reminders, keeping every stored value:

	- The flat remind/escalate hours become the SLA tiers: L1 = remind_after, L2 = remind_after +
	  escalate_after (exactly what the old engine did, one clock), L3 = the new 8-hour default.
	- A site that has messages but never saved the master `enabled` switch (it used to be
	  decorative) stays on: turning capture off by accident during an upgrade would lose data.
	- Open reminders from the old engine have a ToDo already, so they start at level 2; the
	  escalated ones are level 3.
	"""
	if _single("sla_l1_hours") is None:
		remind = flt(_single("remind_after_hours")) or 1.0
		escalate = flt(_single("escalate_after_hours")) or 2.0
		_set("sla_l1_hours", remind)
		_set("sla_l2_hours", remind + escalate)
		_set("sla_l3_hours", 8)

	if _single("enabled") is None and frappe.db.count("Periskope Message") > 0:
		_set("enabled", 1)

	if frappe.db.exists("DocType", "Periskope Reply Reminder"):
		for status, level in (("Open", 2), ("Escalated", 3)):
			frappe.db.sql(
				"""update `tabPeriskope Reply Reminder` set level = %s
				where status = %s and ifnull(level, 0) = 0""",
				(level, status),
			)
