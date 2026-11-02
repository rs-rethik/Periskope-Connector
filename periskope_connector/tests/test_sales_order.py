"""A Sales Order from a customer's WhatsApp message (api/sales_order.py): a Draft in ERPNext with the
customer, items and delivery date, the message kept on it, and its chip on the message."""

import frappe
from frappe.utils import add_days, nowdate

from periskope_connector.api import inbox
from periskope_connector.api import sales_order as so
from periskope_connector.tests.test_crm_panel import PanelCase

IGNORE_TEST_RECORD_DEPENDENCIES = test_ignore = ["Customer", "Item", "Sales Order", "Company"]
ITEM = "_Test PSO Cotton 30s"
CUSTOMER_NAME = "_Test PSO Fabrics"


def _record(doctype: str, name: str, values: dict) -> str:
	if not frappe.db.exists(doctype, name):
		frappe.get_doc({"doctype": doctype, **values}).insert(ignore_permissions=True)
	return name


class SalesOrderCase(PanelCase):
	def setUp(self):
		if not so.available():
			self.skipTest("ERPNext is not installed")
		super().setUp()
		if not frappe.db.exists("UOM", "Nos"):
			frappe.get_doc({"doctype": "UOM", "uom_name": "Nos"}).insert(ignore_permissions=True)
		group = _record("Item Group", "_Test PSO Group", {"item_group_name": "_Test PSO Group"})
		item = {"item_code": ITEM, "item_name": "Cotton 30s", "item_group": group, "stock_uom": "Nos"}
		if frappe.get_meta("Item").has_field("gst_hsn_code"):  # India Compliance asks for it
			item["gst_hsn_code"] = _record(
				"GST HSN Code", "520522", {"hsn_code": "520522", "description": "Cotton yarn"}
			)
		_record("Item", ITEM, item)
		customers = _record(
			"Customer Group", "_Test PSO Customers", {"customer_group_name": "_Test PSO Customers"}
		)
		_record("Customer", CUSTOMER_NAME, {"customer_name": CUSTOMER_NAME, "customer_group": customers})
		self.chat = self._msg(body="Please book 500 kg of 30s cotton, delivery next week")
		self.message = frappe.db.get_value(
			"Periskope Message", {"periskope_chat": self.chat, "direction": "Incoming"}
		)


class TestSalesOrder(SalesOrderCase):
	def test_the_form_starts_with_the_series_and_a_delivery_date(self):
		d = so.so_defaults(self.chat, self.message)
		self.assertEqual(d["delivery_date"], add_days(nowdate(), so.DELIVERY_DAYS))
		self.assertEqual(d["naming_series"], (d["series"] or [None])[0])
		self.assertTrue(d["can_create"])

	def test_items_and_customers_are_found_by_name(self):
		self.assertIn(ITEM, [r.value for r in so.search("Item", "Cotton 30")])
		self.assertIn(CUSTOMER_NAME, [r.value for r in so.search("Customer", "PSO Fab")])
		self.assertRaises(frappe.ValidationError, so.search, "User", "")

	def test_a_draft_order_keeps_the_message_and_shows_on_it(self):
		if not frappe.db.get_single_value("Global Defaults", "default_company"):
			self.skipTest("No default company on this site")
		made = so.create_sales_order_from_message(
			self.chat,
			self.message,
			CUSTOMER_NAME,
			[{"item_code": ITEM, "qty": 500, "rate": 245}],
			delivery_date=add_days(nowdate(), 7),
			naming_series=(so._series() or [None])[0],
			note="Confirmed on call",
		)
		order = frappe.get_doc("Sales Order", made["name"])
		self.assertEqual((order.docstatus, order.customer), (0, CUSTOMER_NAME))
		self.assertEqual((order.items[0].item_code, order.items[0].qty), (ITEM, 500))
		self.assertEqual(order.get("periskope_message"), self.message)
		comment = frappe.db.get_value(
			"Comment", {"reference_doctype": "Sales Order", "reference_name": order.name}, "content"
		)
		self.assertIn("500 kg of 30s cotton", comment)
		self.assertIn("Confirmed on call", comment)
		rows = inbox.get_messages(self.chat)["messages"]
		self.assertIn(order.name, next(r for r in rows if r.name == self.message).orders)

	def test_wrong_input_is_refused_before_anything_is_made(self):
		before = frappe.db.count("Sales Order")
		items = [{"item_code": ITEM, "qty": 1, "rate": 1}]
		mine = frappe.db.get_value("Periskope Message", self.message, "chat_id")
		cases = [
			(CUSTOMER_NAME, []),  # no item
			(CUSTOMER_NAME, [{"item_code": ITEM, "qty": 0}]),  # no quantity
			("_Test No Such Customer", items),
		]
		for customer, lines in cases:
			self.assertRaises(
				frappe.ValidationError,
				so.create_sales_order_from_message,
				self.chat,
				self.message,
				customer,
				lines,
				naming_series=(so._series() or [None])[0],
			)
		self.assertRaises(
			frappe.ValidationError,
			so.create_sales_order_from_message,
			self.chat,
			self.message,
			CUSTOMER_NAME,
			items,
			delivery_date=add_days(nowdate(), -1),  # in the past
			naming_series=(so._series() or [None])[0],
		)
		ours = frappe.get_doc(
			{
				"doctype": "Periskope Message",
				"message_id": f"true_{mine}_OWN",
				"direction": "Outgoing",
				"chat_id": mine,
				"periskope_chat": self.chat,
				"body": "Sure",
			}
		).insert(ignore_permissions=True)
		self.assertRaises(
			frappe.ValidationError,
			so.create_sales_order_from_message,
			self.chat,
			ours.name,
			CUSTOMER_NAME,
			items,
		)
		self.assertEqual(frappe.db.count("Sales Order"), before)
