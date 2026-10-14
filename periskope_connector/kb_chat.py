"""The admins' chat with the agent (desk page "Periskope Assistant").

Admins build the knowledge base by talking ("Our delivery time for Chennai is 2 days, other states
5 days"), change or retire entries the same way, or upload a price list, FAQ or catalogue. The AI
turns that into knowledge entries, and the admin sees a preview and confirms; nothing is saved
before Confirm. The same chat answers questions about the agent: why it replied the way it did,
which tags it learned, what was escalated, what it cannot answer yet.

The AI works in a loop of at most MAX_STEPS: it either calls one of the read-only TOOLS below, or
gives its reply with any proposed changes. Only confirm() writes, and only to Knowledge Entries.
Admins only (System Manager, WhatsApp Manager).
"""

import io
import json
import re
import secrets

import frappe
from frappe import _
from frappe.utils import add_days, getdate, now_datetime, nowdate

from periskope_connector import knowledge, safety
from periskope_connector.ai import assistant
from periskope_connector.api.permission import require_admin
from periskope_connector.system import insert_as_system, save_as_system

MAX_STEPS = 5
MAX_DOC_CHARS = 15000
DRAFT_MINUTES = 30
MAX_CHANGES = 30

SYSTEM_PROMPT = """You are the assistant behind a company's WhatsApp sales agent, talking to an admin. You help them (1) build and maintain the knowledge base the agent answers customers from, and (2) understand what the agent did and why.
Each turn you either call one tool (action "tool", the tool name, and args_json as a JSON object string) or answer (action "reply").
Tools (read only):
- search_knowledge {"query"}: knowledge entries matching words.
- explain_reply {"customer"}: the latest auto replies to a customer (number or name), with the data they used.
- tags_learned {"days"}: tag changes the agent suggested or applied.
- escalations {"date"}: customers escalated or waiting too long that day (YYYY-MM-DD, default today).
- knowledge_gaps {"days"}: customer questions the agent could not answer (it sent the holding message).
- config_overview {}: how the agent is set up.
- pending_suggestions {}: configuration changes waiting for approval.
To add, change or retire knowledge, reply with "changes": operation Add (question, answer, keywords), Modify (entry = the entry's name from search_knowledge, and the new question/answer/keywords) or Remove (entry). Search first before Modify or Remove so you use the right entry name. Write questions the way a customer asks them and answers as the company would reply on WhatsApp: short, factual, one fact per entry, no prices or promises the admin did not give. When a document is uploaded, extract reusable facts from it as Add changes.
When the admin states facts or asks to add, change or remove knowledge, put the changes in "changes" in this same reply: do not ask for permission first, the admin sees a preview with Confirm and Discard buttons, and that is the confirmation. Split separate facts into separate entries. Ask a question only when the admin's message is too unclear to write any entry.
Your reply text is plain language for the admin, as plain text: no markdown symbols such as ** or #; use short lines and "- " for lists. Never invent data: if a tool shows nothing, say so. Say briefly what you prepared."""

SCHEMA = {
	"type": "object",
	"properties": {
		"action": {"type": "string", "enum": ["tool", "reply"]},
		"tool": {
			"type": "string",
			"enum": [
				"",
				"search_knowledge",
				"explain_reply",
				"tags_learned",
				"escalations",
				"knowledge_gaps",
				"config_overview",
				"pending_suggestions",
			],
		},
		"args_json": {"type": "string"},
		"reply": {"type": "string"},
		"changes": {
			"type": "array",
			"items": {
				"type": "object",
				"properties": {
					"operation": {"type": "string", "enum": ["Add", "Modify", "Remove"]},
					"entry": {"type": "string"},
					"question": {"type": "string"},
					"answer": {"type": "string"},
					"keywords": {"type": "string"},
				},
				"required": ["operation", "entry", "question", "answer", "keywords"],
				"additionalProperties": False,
			},
		},
	},
	"required": ["action", "tool", "args_json", "reply", "changes"],
	"additionalProperties": False,
}


# --- Read-only tools ----------------------------------------------------------------------------


def search_knowledge(query: str = "") -> list[dict]:
	entries = frappe.get_all(
		"Periskope Knowledge Entry",
		filters={"status": ["!=", "Retired"]},
		fields=["name", "question", "answer", "keywords", "status"],
		order_by="modified desc",
		limit=500,
	)
	if query:
		words = knowledge.words(query)
		scored = [
			(
				len(
					words
					& (
						knowledge.words(e.question)
						| knowledge.words(e.keywords or "")
						| knowledge.words(e.answer)
					)
				),
				e,
			)
			for e in entries
		]
		entries = [e for score, e in sorted(scored, key=lambda s: s[0], reverse=True) if score][:15]
	return [dict(e) for e in entries[:15]]


def explain_reply(customer: str = "") -> list[dict]:
	digits = re.sub(r"\D", "", customer or "")
	filters = {"status": ["in", ["Sent", "Holding Sent", "Dry Run", "Failed", "Skipped"]]}
	if digits:
		filters["chat_id"] = ["like", f"%{digits[-10:]}%"]
	else:
		chats = frappe.get_all(
			"Periskope Chat", filters={"chat_name": ["like", f"%{customer}%"]}, pluck="name", limit=5
		)
		if not chats:
			return []
		filters["chat_id"] = ["in", chats]
	rows = frappe.get_all(
		"Periskope Auto Reply",
		filters=filters,
		fields=[
			"name",
			"chat_id",
			"status",
			"customer_text",
			"reply_text",
			"confidence",
			"sources",
			"cancel_reason",
			"modified",
		],
		order_by="modified desc",
		limit=3,
	)
	for row in rows:
		used = []
		for source in [s.strip() for s in (row.sources or "").split(",") if s.strip()]:
			kind, _sep, name = source.partition(":")
			if kind == "KB":
				used.append(
					"Knowledge: "
					+ (frappe.db.get_value("Periskope Knowledge Entry", name, "question") or name)
				)
			else:
				used.append(f"{kind.title()}: {name}")
		row["data_used"] = used
		row["chat"] = frappe.db.get_value("Periskope Chat", row.chat_id, "chat_name") or ""
		row.pop("sources", None)
		row.pop("chat_id", None)
	return [dict(r) for r in rows]


def tags_learned(days: int = 7) -> list[dict]:
	return [
		dict(r)
		for r in frappe.get_all(
			"Periskope Suggestion",
			filters={"kind": "Tag", "creation": [">=", add_days(now_datetime(), -int(days or 7))]},
			fields=["summary", "operation", "status", "reason", "creation"],
			order_by="creation desc",
			limit=20,
		)
	]


def escalations(date: str = "") -> list[dict]:
	day = getdate(date) if date else getdate(nowdate())
	rows = frappe.get_all(
		"Periskope Reply Reminder",
		filters={"level": [">=", 1], "modified": ["between", [f"{day} 00:00:00", f"{day} 23:59:59"]]},
		fields=["chat_id", "contact_number", "status", "level", "rep", "waiting_since", "message_preview"],
		order_by="level desc",
		limit=20,
	)
	for row in rows:
		row["chat"] = frappe.db.get_value("Periskope Chat", row.chat_id, "chat_name") or row.contact_number
		row.pop("chat_id", None)
	return [dict(r) for r in rows]


def knowledge_gaps(days: int = 14) -> list[dict]:
	rows = frappe.get_all(
		"Periskope Auto Reply",
		filters={
			"status": ["in", ["Holding Sent", "Dry Run"]],
			"modified": [">=", add_days(now_datetime(), -int(days or 14))],
			"customer_text": ["is", "set"],
		},
		fields=["customer_text", "modified", "status", "cancel_reason"],
		order_by="modified desc",
		limit=60,
	)
	seen, gaps = set(), []
	for row in rows:
		if row.status == "Dry Run" and row.cancel_reason != "Holding message":
			continue  # a dry run that had an answer
		text = (row.customer_text or "").strip()[:300]
		key = " ".join(sorted(knowledge.words(text)))[:120]
		if text and key not in seen:
			seen.add(key)
			gaps.append({"question": text, "when": str(row.modified)})
	return gaps[:15]


def config_overview() -> dict:
	settings = frappe.get_doc("Periskope Settings")
	return {
		"agent_on": bool(settings.enable_agent),
		"auto_reply_on": bool(settings.enable_auto_reply),
		"test_mode": safety.test_mode(),
		"reply_delay_minutes": settings.reply_delay_minutes,
		"holding_message": settings.holding_message,
		"tags": frappe.get_all("Periskope Tag", filters={"active": 1}, pluck="name"),
		"keyword_rules": frappe.get_all("Periskope Keyword Rule", filters={"enabled": 1}, pluck="rule_name"),
		"sla_levels": [
			f"after {r.wait_minutes:g} min ({r.min_severity}+): {r.action}"
			for r in frappe.get_all(
				"Periskope SLA Level",
				fields=["wait_minutes", "min_severity", "action"],
				order_by="wait_minutes asc",
			)
		],
		"knowledge_entries": frappe.db.count("Periskope Knowledge Entry", {"status": "Approved"}),
		"approvers": settings.approver_emails,
	}


def pending_suggestions() -> list[dict]:
	return [
		dict(r)
		for r in frappe.get_all(
			"Periskope Suggestion",
			filters={"status": "Pending"},
			fields=["kind", "operation", "summary", "reason"],
			order_by="creation desc",
			limit=20,
		)
	]


TOOLS = {
	"search_knowledge": search_knowledge,
	"explain_reply": explain_reply,
	"tags_learned": tags_learned,
	"escalations": escalations,
	"knowledge_gaps": knowledge_gaps,
	"config_overview": config_overview,
	"pending_suggestions": pending_suggestions,
}


# --- Documents ----------------------------------------------------------------------------------


def document_text(file_url: str) -> str:
	"""The text of an uploaded PDF, spreadsheet, Word file, CSV or text file (cut at MAX_DOC_CHARS)."""
	file = frappe.get_doc("File", {"file_url": file_url})
	content = file.get_content()
	name = (file.file_name or "").lower()
	if isinstance(content, str):
		content = content.encode()
	if name.endswith(".pdf"):
		from pypdf import PdfReader

		text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages)
	elif name.endswith((".xlsx", ".xlsm")):
		from openpyxl import load_workbook

		book = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
		lines = []
		for sheet in book.worksheets:
			for row in sheet.iter_rows(values_only=True):
				cells = [str(c) for c in row if c not in (None, "")]
				if cells:
					lines.append(" | ".join(cells))
		text = "\n".join(lines)
	elif name.endswith(".docx"):
		from docx import Document

		text = "\n".join(p.text for p in Document(io.BytesIO(content)).paragraphs)
	else:
		text = content.decode("utf-8", errors="replace")
	return text[:MAX_DOC_CHARS]


# --- The chat -----------------------------------------------------------------------------------


def _draft(changes: list[dict]) -> tuple[str | None, list[dict]]:
	"""Check the proposed changes and keep them for confirm(). Returns (token, preview)."""
	preview = []
	for change in changes[:MAX_CHANGES]:
		op = change.get("operation")
		entry = (change.get("entry") or "").strip()
		if op in ("Modify", "Remove"):
			current = frappe.db.get_value(
				"Periskope Knowledge Entry", entry, ["question", "answer", "keywords"], as_dict=True
			)
			if not current:
				continue
			preview.append(
				{
					"operation": op,
					"entry": entry,
					"before": dict(current),
					"question": (change.get("question") or current.question).strip(),
					"answer": (change.get("answer") or current.answer).strip(),
					"keywords": (change.get("keywords") or current.keywords or "").strip(),
				}
			)
		elif op == "Add" and (change.get("question") or "").strip() and (change.get("answer") or "").strip():
			preview.append(
				{
					"operation": "Add",
					"entry": "",
					"question": change["question"].strip(),
					"answer": change["answer"].strip(),
					"keywords": (change.get("keywords") or "").strip(),
				}
			)
	if not preview:
		return None, []
	token = secrets.token_urlsafe(16)
	frappe.cache().set_value(
		f"periskope_kb_draft:{token}",
		{"user": frappe.session.user, "changes": preview},
		expires_in_sec=DRAFT_MINUTES * 60,
	)
	return token, preview


@frappe.whitelist()
def chat(message: str, history: str | list | None = None, file_url: str | None = None) -> dict:
	"""One turn: {reply, preview, token, tools}. `history` is the earlier turns, [{role, text}]."""
	require_admin()
	ready, reason = assistant.readiness()
	if not ready:
		return {
			"reply": _("The AI cannot be used right now: {0}").format(
				_("the monthly budget is used up") if reason == "budget" else _("no AI key is set")
			),
			"preview": [],
			"token": None,
			"tools": [],
		}
	turns = json.loads(history) if isinstance(history, str) else (history or [])
	lines = [f"{t.get('role', 'admin').upper()}: {t.get('text', '')}" for t in turns[-12:]]
	lines.append(f"ADMIN: {message}")
	if file_url:
		lines.append(f"UPLOADED DOCUMENT ({file_url.rsplit('/', 1)[-1]}):\n{document_text(file_url)}")
	used = []
	for _step in range(MAX_STEPS):
		result = assistant.complete_json(SYSTEM_PROMPT, "\n\n".join(lines), SCHEMA, 3000)
		if result.get("action") == "tool" and result.get("tool") in TOOLS:
			try:
				args = json.loads(result.get("args_json") or "{}")
				output = TOOLS[result["tool"]](**(args if isinstance(args, dict) else {}))
			except TypeError:
				output = TOOLS[result["tool"]]()
			used.append(result["tool"])
			lines.append(f"TOOL {result['tool']} RESULT:\n{json.dumps(output, default=str)[:6000]}")
			continue
		token, preview = _draft(result.get("changes") or [])
		return {"reply": result.get("reply") or "", "preview": preview, "token": token, "tools": used}
	return {
		"reply": _("I could not finish that in a few steps. Please ask more specifically."),
		"preview": [],
		"token": None,
		"tools": used,
	}


@frappe.whitelist()
def confirm(token: str) -> dict:
	"""Save a previewed set of knowledge changes. Returns how many were saved."""
	require_admin()
	key = f"periskope_kb_draft:{token}"
	draft = frappe.cache().get_value(key)
	if not draft or draft.get("user") != frappe.session.user:
		frappe.throw(_("This preview has expired. Ask again to get a new one."))
	frappe.cache().delete_value(key)
	saved = 0
	for change in draft["changes"]:
		if change["operation"] == "Add":
			doc = insert_as_system(
				{
					"doctype": "Periskope Knowledge Entry",
					"question": change["question"],
					"answer": change["answer"],
					"keywords": change["keywords"],
					"status": "Approved",
					"source": "Manual",
					"confidence": 100,
				}
			)
		else:
			if not frappe.db.exists("Periskope Knowledge Entry", change["entry"]):
				continue
			doc = frappe.get_doc("Periskope Knowledge Entry", change["entry"])
			if change["operation"] == "Remove":
				doc.status = "Retired"
			else:
				doc.update({k: change[k] for k in ("question", "answer", "keywords")})
				doc.status = "Approved"
			save_as_system(doc)
		safety.log(
			"Knowledge Change",
			safety.LIVE,
			f"{change['operation']}: {change['question'][:100]}",
			origin="KB Chat",
			target_doctype="Periskope Knowledge Entry",
			target_name=doc.name,
			detail={"user": frappe.session.user},
		)
		saved += 1
	return {"saved": saved}
