"""A Sales Order straight from a customer's WhatsApp message (ERPNext), beside "Create deal".

The order is saved as a Draft in ERPNext with what the chat can give: the customer (from the chat's
deal, or picked), the items with quantity and rate, the delivery date and the naming series. It keeps
the message (the "From WhatsApp Message" field, and a comment quoting it) and the deal it belongs to
(custom_crm_deal, where the site has it). A site's own required fields (payment terms, delivery
terms, sales team...) are filled in ERPNext before the order is submitted: ERPNext asks for them then.
Everything follows the user's ERPNext permissions.
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, flt, getdate, nowdate

from periskope_connector import access
from periskope_connector.api.crm_panel import _chat, _customer_message, _sender
from periskope_connector.text_rules import readable_text

DELIVERY_DAYS = 7  # the delivery date the form starts with
MAX_ITEMS = 50


def available() -> bool:
	return bool(frappe.db.exists("DocType", "Sales Order"))


def _deal_of(row, msg) -> str | None:
	"""The deal the order belongs to: the chat's deal, else a deal made from this message."""
	if row.crm_deal:
		return row.crm_deal
	if frappe.get_meta("CRM Deal").has_field("periskope_message"):
		return frappe.db.get_value("CRM Deal", {"periskope_message": msg.name})
	return None


def _customers_of(deal: str | None) -> list[str]:
	"""The ERPNext customers the deal is linked to (custom_customer / erpnext_customer, or
	Customer.crm_deal), where the site has those fields."""
	if not deal:
		return []
	found = []
	deal_meta = frappe.get_meta("CRM Deal")
	for field in ("custom_customer", "erpnext_customer"):
		if deal_meta.has_field(field):
			found.append(frappe.db.get_value("CRM Deal", deal, field))
	if frappe.get_meta("Customer").has_field("crm_deal"):
		found += frappe.get_all("Customer", filters={"crm_deal": deal}, pluck="name")
	return [c for c in dict.fromkeys(found) if c and frappe.db.exists("Customer", c)]


def _series() -> list[str]:
	field = frappe.get_meta("Sales Order").get_field("naming_series")
	return [o for o in (field.options or "").split("\n") if o.strip()] if field else []


def _company() -> str | None:
	return frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)


def _price_list() -> str | None:
	return frappe.db.get_single_value("Selling Settings", "selling_price_list")


@frappe.whitelist()
def so_defaults(chat_name: str, message: str) -> dict:
	"""What the "Create sales order" form starts with."""
	access.require_inbox_user()
	if not available():
		frappe.throw(_("ERPNext is not installed on this site."))
	row = _chat(chat_name)
	msg = _customer_message(row, message)
	deal = _deal_of(row, msg)
	customers = _customers_of(deal)
	series = _series()
	return {
		"deal": deal,
		"customers": customers,
		"customer": customers[0] if len(customers) == 1 else None,
		"series": series,
		"naming_series": series[0] if series else None,
		"company": _company(),
		"delivery_date": add_days(nowdate(), DELIVERY_DAYS),
		"can_create": bool(frappe.has_permission("Sales Order", "create")),
	}


@frappe.whitelist()
def search(doctype: str, query: str = "") -> list[dict]:
	"""Customers or sellable items for the form, by name or code; an item comes with its price in the
	default selling price list."""
	access.require_inbox_user()
	if doctype not in ("Customer", "Item") or not available():
		frappe.throw(_("Cannot search {0}").format(doctype))
	like = f"%{(query or '').strip()}%"
	if doctype == "Customer":
		return frappe.get_list(
			"Customer",
			filters={"disabled": 0},
			or_filters=[["name", "like", like], ["customer_name", "like", like]],
			fields=["name as value", "customer_name as label"],
			order_by="customer_name asc",
			limit=20,
		)
	rows = frappe.get_list(
		"Item",
		filters={"disabled": 0, "is_sales_item": 1},
		or_filters=[["name", "like", like], ["item_name", "like", like]],
		fields=["name as value", "item_name as label", "stock_uom as uom"],
		order_by="item_name asc",
		limit=20,
	)
	price_list = _price_list()
	if rows and price_list:
		prices = dict(
			frappe.get_all(
				"Item Price",
				filters={"price_list": price_list, "item_code": ["in", [r.value for r in rows]]},
				fields=["item_code", "price_list_rate"],
				as_list=True,
			)
		)
		for r in rows:
			r.rate = prices.get(r.value) or 0
	return rows


@frappe.whitelist(methods=["POST"])
def create_sales_order_from_message(
	chat_name: str,
	message: str,
	customer: str,
	items: str | list,
	delivery_date: str | None = None,
	naming_series: str | None = None,
	note: str = "",
) -> dict:
	"""A Draft Sales Order from the customer's message. Returns {name, route}."""
	access.require_inbox_user()
	if not available():
		frappe.throw(_("ERPNext is not installed on this site."))
	row = _chat(chat_name)
	msg = _customer_message(row, message)
	frappe.has_permission("Sales Order", "create", throw=True)
	if not customer or not frappe.db.exists("Customer", customer):
		frappe.throw(_("Choose the customer."))
	frappe.has_permission("Customer", "read", customer, throw=True)
	lines = [
		i
		for i in (frappe.parse_json(items) if isinstance(items, str) else items or [])[:MAX_ITEMS]
		if i.get("item_code")
	]
	if not lines:
		frappe.throw(_("Add at least one item."))
	for line in lines:
		if not frappe.db.exists("Item", {"name": line["item_code"], "disabled": 0}):
			frappe.throw(_("Unknown item {0}").format(line["item_code"]))
		if flt(line.get("qty")) <= 0:
			frappe.throw(_("Enter a quantity for {0}.").format(line["item_code"]))
	delivery = getdate(delivery_date or add_days(nowdate(), DELIVERY_DAYS))
	if delivery < getdate(nowdate()):
		frappe.throw(_("The delivery date cannot be in the past."))
	series = _series()
	if series and naming_series not in series:
		frappe.throw(_("Choose the naming series."))

	deal = _deal_of(row, msg)
	meta = frappe.get_meta("Sales Order")
	values = {
		"doctype": "Sales Order",
		"customer": customer,
		"company": _company(),
		"transaction_date": nowdate(),
		"delivery_date": delivery,
		"order_type": "Sales",
		"items": [
			{
				"item_code": line["item_code"],
				"qty": flt(line.get("qty")),
				"rate": flt(line.get("rate")),
				"delivery_date": delivery,
			}
			for line in lines
		],
	}
	if series:
		values["naming_series"] = naming_series
	if deal and meta.has_field("custom_crm_deal"):
		values["custom_crm_deal"] = deal
	if meta.has_field("periskope_message"):
		values["periskope_message"] = msg.name
	doc = frappe.get_doc(values)
	# A draft: the site's own required fields (payment terms, sales team...) are filled in ERPNext
	# before it is submitted, where ERPNext asks for them.
	doc.flags.ignore_mandatory = True
	doc.insert()

	_phone, sender = _sender(row, msg)
	text = readable_text(msg) or _("[{0}]").format(msg.message_type or _("message"))
	when = frappe.utils.format_datetime(msg.message_timestamp, "d MMM yyyy, h:mm a")
	extra = frappe.utils.escape_html((note or "").strip()).replace("\n", "<br>")
	doc.add_comment(
		"Comment",
		(f"<p>{extra}</p>" if extra else "")
		+ f"<p>{_('From WhatsApp')}: <b>{frappe.utils.escape_html(sender or row.chat_name or '')}</b>"
		+ f" · {when}</p>"
		+ f"<blockquote>{frappe.utils.escape_html(text).replace(chr(10), '<br>')}</blockquote>"
		+ f'<p><a href="/whatsapp/inbox/{row.name}">{_("Open the WhatsApp chat")}</a></p>',
	)
	return {"name": doc.name, "route": f"/app/sales-order/{doc.name}", "items": cint(len(lines))}
