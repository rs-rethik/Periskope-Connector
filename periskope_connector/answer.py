"""Work out what to tell a customer, from the system's own data only.

Sources, in the order they are collected:
- the approved knowledge base entries that match what the customer wrote,
- items with their prices and stock from ERPNext (when it is installed),
- the customer's open sales orders from ERPNext, found from the chat's phone number.

The AI may only use what is listed. The code then checks the answer again: it must name the
sources it used and every number in it must come from a source or from the customer's own text.
When there is nothing to answer from, the AI is not even asked.
"""

import re

import frappe
from frappe.utils import flt

from periskope_connector import knowledge
from periskope_connector.ai import assistant
from periskope_connector.linking import number_key

MAX_ITEMS = 5
MAX_ORDERS = 5
MIN_TOKEN = 4
STOP = knowledge.STOP | {
	"price",
	"rate",
	"cost",
	"available",
	"stock",
	"need",
	"want",
	"send",
	"give",
	"tell",
	"about",
	"regarding",
	"with",
	"from",
	"this",
	"that",
	"much",
	"many",
	"when",
	"will",
	"kindly",
	"hello",
	"thanks",
	"thank",
}

SYSTEM_PROMPT = """You write the reply to a customer's WhatsApp message for a company. You are given the customer's messages and a list of DATA sources. Answer ONLY from the data.
- If the data does not contain what the customer asks, set can_answer to false and leave answer empty. Never guess, never invent prices, stock, dates, or promises.
- If the customer asks several things and the data covers only some, set can_answer to false.
- Reply in the customer's language and style, short and friendly, plain text, no markdown, no greeting more than one word, no signature.
- confidence is 0-100: how sure you are the answer is fully supported by the data.
- used_sources lists the ids of the sources you used."""

SCHEMA = {
	"type": "object",
	"properties": {
		"can_answer": {"type": "boolean"},
		"answer": {"type": "string"},
		"confidence": {"type": "integer"},
		"used_sources": {"type": "array", "items": {"type": "string"}},
	},
	"required": ["can_answer", "answer", "confidence", "used_sources"],
	"additionalProperties": False,
}


def _tokens(text: str) -> list[str]:
	seen = []
	for word in re.findall(r"[\w\-/.]+", (text or "").lower()):
		word = word.strip("-./")
		if len(word) >= MIN_TOKEN and word not in STOP and word not in seen:
			seen.append(word)
	return seen[:8]


def _items(text: str) -> list[dict]:
	if not frappe.db.exists("DocType", "Item"):
		return []
	tokens = _tokens(text)
	if not tokens:
		return []
	or_filters = []
	for token in tokens:
		like = f"%{token}%"
		or_filters += [
			["item_name", "like", like],
			["item_code", "like", like],
			["description", "like", like],
		]
	candidates = frappe.get_all(
		"Item",
		filters={"disabled": 0, "is_sales_item": 1},
		or_filters=or_filters,
		fields=["name", "item_name", "item_group", "stock_uom", "description"],
		limit=40,
	)

	def hits(item) -> int:
		hay = f"{item.name} {item.item_name} {item.item_group}".lower()
		return sum(1 for t in tokens if t in hay)

	out = []
	for item in sorted(candidates, key=hits, reverse=True)[:MAX_ITEMS]:
		if not hits(item):
			continue
		price = frappe.get_all(
			"Item Price",
			filters={"item_code": item.name, "selling": 1},
			fields=["price_list_rate", "currency", "price_list", "uom"],
			order_by="valid_from desc, modified desc",
			limit=1,
		)
		stock = frappe.db.sql(
			"select coalesce(sum(actual_qty - reserved_qty), 0) from `tabBin` where item_code = %s",
			(item.name,),
		)
		facts = {
			"item": item.name,
			"name": item.item_name,
			"group": item.item_group,
			"unit": item.stock_uom,
			"description": re.sub(r"<[^>]+>", " ", item.description or "")[:300].strip(),
			"in_stock": flt(stock[0][0]) if stock else None,
		}
		if price:
			facts["price"] = (
				f"{price[0].price_list_rate} {price[0].currency or ''} per {price[0].uom or item.stock_uom} ({price[0].price_list})".strip()
			)
		out.append(facts)
	return out


def _customers(number: str) -> list[str]:
	key = number_key(number)
	if not key or not frappe.db.exists("DocType", "Customer"):
		return []
	parents = frappe.get_all(
		"Contact Phone", filters={"phone": ["like", f"%{key}"]}, pluck="parent", limit=10
	)
	if not parents:
		return []
	return frappe.get_all(
		"Dynamic Link",
		filters={"parent": ["in", parents], "parenttype": "Contact", "link_doctype": "Customer"},
		pluck="link_name",
		limit=5,
	)


def _orders(number: str) -> list[dict]:
	customers = _customers(number)
	if not customers or not frappe.db.exists("DocType", "Sales Order"):
		return []
	return frappe.get_all(
		"Sales Order",
		filters={
			"customer": ["in", customers],
			"docstatus": 1,
			"status": ["not in", ["Closed", "Cancelled"]],
		},
		fields=[
			"name",
			"transaction_date",
			"delivery_date",
			"status",
			"per_delivered",
			"per_billed",
			"grand_total",
			"currency",
		],
		order_by="transaction_date desc",
		limit=MAX_ORDERS,
	)


def gather(text: str, contact_number: str = "", is_group: bool = False) -> list[dict]:
	"""The data sources for a customer's text: [{id, title, text}]."""
	sources = []
	for entry in knowledge.find(text):
		sources.append({"id": f"KB:{entry.name}", "title": entry.question, "text": entry.answer})
	for item in _items(text):
		lines = ", ".join(f"{k}: {v}" for k, v in item.items() if v not in (None, "") and k != "item")
		sources.append({"id": f"ITEM:{item['item']}", "title": item["name"] or item["item"], "text": lines})
	if not is_group:
		for order in _orders(contact_number):
			lines = ", ".join(f"{k}: {v}" for k, v in order.items() if v not in (None, "") and k != "name")
			sources.append({"id": f"ORDER:{order.name}", "title": f"Sales Order {order.name}", "text": lines})
	return sources


def _number(raw: str) -> str:
	"""One number written one way: no thousands commas and no trailing decimal zeros, so the
	245.0 of an Item Price matches the 245 in the AI's answer."""
	n = raw.replace(",", "")
	return n.rstrip("0").rstrip(".") if "." in n else n


def _numbers(text: str) -> set[str]:
	return {n for n in (_number(raw) for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")) if len(n) >= 2}


def grounded(answer: str, sources: list[dict], customer_text: str) -> bool:
	"""Every number in the answer is in a source or in what the customer wrote."""
	allowed = _numbers(customer_text)
	for source in sources:
		allowed |= _numbers(source["text"]) | _numbers(source["title"])
	return _numbers(answer) <= allowed


def build(customer_text: str, contact_number: str = "", is_group: bool = False) -> dict:
	"""{can_answer, answer, confidence, used_sources, sources}. can_answer is False when there is
	no data, the AI is unsure, or the answer fails the checks."""
	sources = gather(customer_text, contact_number, is_group)
	result = {"can_answer": False, "answer": "", "confidence": 0, "used_sources": [], "sources": sources}
	if not sources:
		return result
	data = "\n".join(f"[{s['id']}] {s['title']}: {s['text']}" for s in sources)
	reply = assistant.complete_json(
		SYSTEM_PROMPT, f"DATA:\n{data}\n\nCUSTOMER MESSAGES:\n{customer_text}", SCHEMA, 800
	)
	ids = {s["id"] for s in sources}
	used = [i for i in reply.get("used_sources") or [] if i in ids]
	answer = (reply.get("answer") or "").strip()
	ok = (
		bool(reply.get("can_answer"))
		and bool(answer)
		and bool(used)
		and grounded(answer, sources, customer_text)
	)
	return {
		"can_answer": ok,
		"answer": answer if ok else "",
		"confidence": int(reply.get("confidence") or 0) if ok else 0,
		"used_sources": used,
		"sources": sources,
	}
