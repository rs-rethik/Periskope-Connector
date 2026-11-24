"""Numbers and groups whose messages could not be matched to a CRM lead or deal.

Sales adds them to the CRM from this list. Messages are kept either way, but nothing is added to a
timeline for them. Team groups are left out: they have no single customer to add.
"""

import frappe
from frappe import _
from frappe.utils import add_days, escape_html, get_url, today

from periskope_connector import safety
from periskope_connector.linking import by_mobile

UNMATCHED = ("No Match", "Internal")
MAX_EMAIL_ROWS = 50


def columns() -> list[dict]:
	return [
		{"label": _("Type"), "fieldname": "kind", "fieldtype": "Data", "width": 90},
		{"label": _("Number or Group"), "fieldname": "who", "fieldtype": "Data", "width": 190},
		{"label": _("Name"), "fieldname": "name", "fieldtype": "Data", "width": 170},
		{"label": _("Messages"), "fieldname": "messages", "fieldtype": "Int", "width": 90},
		{"label": _("Last Message"), "fieldname": "last_message", "fieldtype": "Datetime", "width": 160},
		{"label": _("Category"), "fieldname": "category", "fieldtype": "Data", "width": 140},
		{
			"label": _("Sales Person"),
			"fieldname": "sales_person",
			"fieldtype": "Link",
			"options": "Sales Person",
			"width": 130,
		},
		{"label": _("Last Text"), "fieldname": "text", "fieldtype": "Data", "width": 320},
	]


def get_rows(from_date=None, to_date=None, numbers: list[str] | None = None) -> list[dict]:
	"""One row per unmatched customer number, and per unmatched customer group, newest first.
	`numbers`: only the messages on these WhatsApp numbers (None: every number)."""
	from_date = from_date or add_days(today(), -7)
	to_date = to_date or today()
	rows = frappe.db.sql(
		"""
		select
			if(m.is_group = 1, 'Group', 'Customer') as kind,
			if(m.is_group = 1, m.chat_id, ifnull(nullif(m.customer_phone, ''), m.contact_number)) as who,
			max(if(m.is_group = 1, m.chat_name, m.sender_name)) as name,
			count(*) as messages,
			max(m.message_timestamp) as last_message,
			substring_index(group_concat(nullif(m.ai_category, '') order by m.message_timestamp desc), ',', 1) as category,
			max(m.sales_person) as sales_person,
			substring_index(group_concat(left(ifnull(m.body, ''), 200) order by m.message_timestamp desc separator '\\x1f'), '\\x1f', 1) as text
		from `tabPeriskope Message` m
		left join `tabPeriskope Group` g on g.chat_id = m.chat_id
		where m.link_status in %(status)s
			and date(m.message_timestamp) between %(from)s and %(to)s
			and (m.is_group = 0 or g.group_type = 'Customer')
			and (%(every)s or m.whatsapp_number in %(numbers)s)
		group by m.is_group, who
		having who is not null and who != ''
		order by last_message desc
		""",
		{
			"status": UNMATCHED,
			"from": from_date,
			"to": to_date,
			"numbers": tuple(numbers or [""]),
			"every": int(numbers is None),
		},
		as_dict=True,
	)
	# A number added to the CRM since its messages arrived is no longer unmatched.
	return [r for r in rows if r.kind == "Group" or not by_mobile(r.who)]


def send_weekly_report():
	"""Scheduler, weekly: email the unmatched numbers of the last 7 days to the managers."""
	from periskope_connector.reminders import get_config

	emails = get_config().emails
	rows = get_rows()
	if not emails or not rows:
		return 0

	shown = rows[:MAX_EMAIL_ROWS]
	body = "".join(
		"<tr>"
		+ "".join(
			f"<td style='padding:4px 8px;border:1px solid #ddd'>{escape_html(str(v or ''))}</td>"
			for v in (
				r.kind,
				r.who,
				r.name,
				r.messages,
				r.last_message,
				(r.category or "").replace("_", " "),
				r.text[:120],
			)
		)
		+ "</tr>"
		for r in shown
	)
	head = "".join(
		f"<th style='padding:4px 8px;border:1px solid #ddd;text-align:left'>{h}</th>"
		for h in (
			_("Type"),
			_("Number or Group"),
			_("Name"),
			_("Messages"),
			_("Last Message"),
			_("Category"),
			_("Last Text"),
		)
	)
	more = f"<p>{_('and {0} more').format(len(rows) - len(shown))}</p>" if len(rows) > len(shown) else ""
	safety.sendmail(
		recipients=emails,
		subject=_("WhatsApp numbers not in the CRM this week ({0})").format(len(rows)),
		message=(
			f"<p>{_('These customers wrote to a company WhatsApp number in the last 7 days but are not on any CRM lead or deal. Please add them.')}</p>"
			f"<table style='border-collapse:collapse;font-size:13px'><tr>{head}</tr>{body}</table>{more}"
			f"<p><a href='{get_url()}/app/query-report/Periskope Unmatched Numbers'>{_('Open the report')}</a></p>"
		),
	)
	return len(rows)
