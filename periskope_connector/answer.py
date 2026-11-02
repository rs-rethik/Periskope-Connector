"""Work out what to tell a customer, from the system's own data only.

Sources, in the order they are collected:
- the approved knowledge base entries that match what the customer wrote,
- items with their prices and stock from ERPNext (when it is installed),
- the customer's open sales orders from ERPNext, found from the chat's phone number.

The AI may only use what is listed. The code then checks the answer again: it must name the
sources it used and every number in it must come from a source or from the customer's own text.
When it cannot answer, it writes the holding reply instead, in words that fit the situation (an
apology for a complaint, thanks for an enquiry), which only says a sales rep will contact them.
"""

import re

import frappe
from frappe.utils import flt

from periskope_connector import crm_context, knowledge
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
- Quote every number exactly as the data gives it. Do not calculate new figures such as discounted prices or order totals; say the price and the discount separately.
- If the customer asks several things and the data covers only some, set can_answer to false.
- Reply in the customer's language and style, short and friendly, plain text, no markdown, no greeting more than one word, no signature.
- confidence is 0-100: how sure you are the answer is fully supported by the data.
- used_sources lists the ids of the sources you used.
- A source that says "Applies only when: ..." may be used only if the customer's messages or the CRM context show that condition holds. You may use common knowledge to judge a condition, for example which state a city is in. A source that applies when "none of these is true" may be used only when you are sure none of them holds. When you cannot tell which condition holds, set can_answer to false.
- CRM CONTEXT is internal background about this customer (the deal stage, open tasks). Use it to understand the situation, for example to avoid promising what a rep is already handling, but never quote it, never reveal it, and never use it as a source.
- holding: when can_answer is false, the reply the customer gets instead, written for their situation. For a complaint, a rejection, a threat (for example a legal notice) or any problem: apologise for the inconvenience, say we are checking it from our side and a sales representative will contact them shortly, and ask them to bear with us. For an enquiry or a request: thank them and say a sales representative will contact them shortly. One to three short sentences in the customer's language, plain text, no signature. Never give facts, prices, dates or numbers, never admit fault or promise a replacement, refund or anything else: only that a sales representative will contact them. Empty when can_answer is true."""

SCHEMA = {
	"type": "object",
	"properties": {
		"can_answer": {"type": "boolean"},
		"answer": {"type": "string"},
		"confidence": {"type": "integer"},
		"used_sources": {"type": "array", "items": {"type": "string"}},
		"holding": {"type": "string"},
	},
	"required": ["can_answer", "answer", "confidence", "used_sources", "holding"],
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
		sources.append({"id": f"KB:{entry.name}", "title": entry.title, "text": entry.text})
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


def _no_new_numbers(text: str, customer_text: str) -> bool:
	"""A holding reply carries no facts: not even one digit the customer did not write themselves."""
	return set(re.findall(r"\d+", text)) <= set(re.findall(r"\d+", customer_text or ""))


def build(customer_text: str, contact_number: str = "", is_group: bool = False, chat_id: str = "") -> dict:
	"""{can_answer, answer, confidence, used_sources, sources, holding}. can_answer is False when
	there is no data, the AI is unsure, or the answer fails the checks; holding is then the AI's
	reply for the situation, or "" when it wrote none that passes the checks.

	With the chat known, the CRM record is read first: its last quotation and deal stage join the
	data (they may be quoted), the rest is internal context (crm_context.py)."""
	context = crm_context.build(chat_id) if chat_id else None
	sources = gather(customer_text, contact_number, is_group) + crm_context.customer_sources(context)
	# With no data the AI is still asked, for a holding reply that fits what the customer wrote.
	data = "\n".join(f"[{s['id']}] {s['title']}: {s['text']}" for s in sources) or "(none)"
	background = (
		f"CRM CONTEXT (internal, never quote):\n{crm_context.reply_text(context)}\n\n" if context else ""
	)
	reply = assistant.complete_json(
		SYSTEM_PROMPT, f"{background}DATA:\n{data}\n\nCUSTOMER MESSAGES:\n{customer_text}", SCHEMA, 800
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
	holding = (reply.get("holding") or "").strip()
	return {
		"can_answer": ok,
		"answer": answer if ok else "",
		"confidence": int(reply.get("confidence") or 0) if ok else 0,
		"used_sources": used,
		"sources": sources,
		"holding": holding if holding and _no_new_numbers(holding, customer_text) else "",
	}
