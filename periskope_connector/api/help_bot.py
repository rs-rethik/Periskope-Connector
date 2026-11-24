"""The Assistant: a chat panel in the WhatsApp app that answers basic questions about the app and the
user's inbox, and downloads the Periskope reports the user may open.

Each question is one structured AI call (ai/assistant.complete_json, so it counts in the same token
budget and works with Jarvis too). The AI gets the user manual from the README, the user's own inbox
counts and the reports they may open. When the user asks for a report it names one of them with the
dates, and the panel shows a download button: the file is made by `download_report`, which checks the
permission again and runs the report as the user, so the AI never sees or hands out report data."""

import os
import re
from urllib.parse import urlencode

import frappe
from frappe import _
from frappe.utils import add_days, getdate, today

from periskope_connector import access, safety
from periskope_connector.ai import assistant

MAX_TURNS = 12  # earlier messages of the conversation sent with each question
MAX_QUESTION = 1000
ASK_LIMIT = 30  # questions per user ...
ASK_WINDOW_SECONDS = 300  # ... in five minutes
MAX_MANUAL = 40000  # characters of the user manual given to the AI

# Reports the Assistant can download, with what each answers (from the README) and its default period.
REPORTS = {
	"Periskope Open Escalations": (
		"Which customers are waiting right now, at what level, and who was told.",
		None,
	),
	"Periskope Response Time": ("How fast each rep answers, in working time.", 30),
	"Periskope Tagging Review": ("How often managers corrected the AI, by tag.", 7),
	"Periskope Lead Source": ("How many leads came from WhatsApp.", 28),
	"Periskope AI Usage": ("Tokens used per day and model.", 30),
	"Periskope Sync Health": ("Messages per company number, to spot a silent number.", None),
	"Periskope Unmatched Numbers": ("Numbers and groups that could not be linked to a lead or deal.", 7),
	"Periskope WhatsApp Funnel": (
		"WhatsApp leads per owner: how many became deals, were won, and the sales orders' value.",
		30,
	),
	"Periskope Auto Reply Quality": (
		"How often the AI could answer customers itself, per rep, and how sure it was.",
		30,
	),
}
FORMATS = ("xlsx", "csv")

SYSTEM = """You are the Assistant inside {brand}, a WhatsApp inbox for a company's sales team, connected \
to Frappe CRM. You help the signed-in user with basic questions: how to do things in the app, what a \
screen or setting means, and what is in their inbox right now. You also download reports for them.

Rules:
- Answer from the user manual and the facts below only. If they do not cover the question, say you do \
not know and suggest asking their manager. Never make up features, numbers, names or steps.
- Keep answers short and practical: a sentence or two, or a few numbered steps. Plain text, no markdown \
headings or tables; "-" bullets and **bold** are fine.
- You cannot change anything in the app, send messages or read chats. Say so if asked, and tell the user \
where to do it themselves.
- Reports: when the user asks to download, export or get a report, set "download" to one of the reports \
below that they may open, with the dates (YYYY-MM-DD) and the format (xlsx unless they ask for CSV), and \
say in "reply" in one short sentence which report it is. Do not repeat the dates in "reply": the download button shows them. Work out dates like "last week" or "this month" \
from today's date. Leave the dates empty for a report without a period, or to use its default period. \
If the report they want is not in the list, say it is not available to them and name the ones that are. \
If it is unclear which report they mean, ask instead of guessing.
- For questions about the company numbers, use the list below exactly: keep each number under the \
service it is connected through, and do not mix them up.
- When not downloading, set "download" to null.

Today is {today}. The user is {user}{manager}.

The user's inbox right now (chats they can see):
{counts}

The company WhatsApp numbers this user can see, and their connection right now:
{numbers}

Test mode (nothing reaches real customers while on): {test_mode}

Reports this user may download:
{reports}

The user manual:
<manual>
{manual}
</manual>"""

SCHEMA = {
	"type": "object",
	"properties": {
		"reply": {"type": "string"},
		"download": {
			"anyOf": [
				{"type": "null"},
				{
					"type": "object",
					"properties": {
						"report": {"type": "string"},
						"from_date": {"type": "string"},
						"to_date": {"type": "string"},
						"format": {"type": "string", "enum": list(FORMATS)},
					},
					"required": ["report", "from_date", "to_date", "format"],
					"additionalProperties": False,
				},
			]
		},
	},
	"required": ["reply", "download"],
	"additionalProperties": False,
}


@frappe.whitelist(methods=["POST"])
def ask(question: str, history: list | str | None = None) -> dict:
	"""Answer one question. `history` is the conversation so far: [{"role": "user"|"assistant",
	"text": ...}]. Returns {"reply": text, "download": {report, from_date, to_date, format, url} | None}."""
	access.require_inbox_user()
	question = (question or "").strip()[:MAX_QUESTION]
	if not question:
		frappe.throw(_("Type a question."))
	_count_ask()
	ready, why = assistant.readiness()
	if not ready:
		return {
			"reply": _("The Assistant is not available: the monthly AI budget is used up.")
			if why == "budget"
			else _("The Assistant needs an AI key. Ask your admin to add one in Settings > AI."),
			"download": None,
		}

	reports = allowed_reports()
	answer = assistant.complete_json(
		_system(reports), _conversation(history, question), SCHEMA, max_tokens=1500
	)
	reply = (answer.get("reply") or "").strip() or _(
		"Sorry, I could not answer that. Try asking another way."
	)
	return {"reply": reply, "download": _download(answer.get("download"), reports)}


def _count_ask():
	"""At most ASK_LIMIT questions per user in ASK_WINDOW_SECONDS: each one is a paid AI call. Counted
	per user, as frappe.rate_limiter does per IP address (a whole office shares one address, so its
	decorator would make the team share one allowance)."""
	key = frappe.cache.make_key(f"periskope:help_bot:asks:{frappe.session.user}")
	if not frappe.cache.get(key):
		frappe.cache.setex(key, ASK_WINDOW_SECONDS, 0)
	if frappe.cache.incrby(key, 1) > ASK_LIMIT:
		frappe.throw(
			_("You have asked many questions in a short time. Please try again in a few minutes."),
			frappe.RateLimitExceededError,
		)


def allowed_reports() -> list[str]:
	"""The Assistant's reports the user may open: the Sales Managers and admins, and the report's
	roles as in Desk. A manager given numbers does not get the app-wide AI Usage report."""
	from periskope_connector import access

	if not access.is_manager():
		return []
	every = access.sees_every_number()
	out = []
	for name in REPORTS:
		if name == "Periskope AI Usage" and not every:
			continue
		if frappe.db.exists("Report", name) and frappe.get_doc("Report", name).is_permitted():
			out.append(name)
	return out


def _download(choice, reports) -> dict | None:
	"""Check the AI's report choice and turn it into a download link; None when it is not usable."""
	if not isinstance(choice, dict) or choice.get("report") not in reports:
		return None
	fmt = choice.get("format") if choice.get("format") in FORMATS else "xlsx"
	from_date, to_date = _dates(choice["report"], choice.get("from_date"), choice.get("to_date"))
	args = {"report": choice["report"], "format": fmt, "from_date": from_date or "", "to_date": to_date or ""}
	return {
		**args,
		"label": choice["report"].replace("Periskope ", "", 1),
		"url": f"/api/method/periskope_connector.api.help_bot.download_report?{urlencode(args)}",
	}


def _dates(report, from_date, to_date) -> tuple[str | None, str | None]:
	"""Valid dates for the report: its default period when the AI gave none or bad ones, and none for a
	report without a period."""
	period = REPORTS[report][1]
	if period is None:
		return None, None
	try:
		end = getdate(to_date) if to_date else getdate(today())
		start = getdate(from_date) if from_date else getdate(add_days(end, -period))
	except Exception:
		end = getdate(today())
		start = getdate(add_days(end, -period))
	if start > end:
		start, end = end, start
	return str(start), str(end)


@frappe.whitelist()
def download_report(report: str, format: str = "xlsx", from_date: str = "", to_date: str = ""):
	"""The report as an Excel or CSV file, run as the signed-in user."""
	access.require_inbox_user()
	if report not in REPORTS or report not in allowed_reports():
		frappe.throw(_("You cannot download this report."), frappe.PermissionError)
	from_date, to_date = _dates(report, from_date, to_date)
	filters = {k: v for k, v in {"from_date": from_date, "to_date": to_date}.items() if v}

	result = frappe.get_doc("Report", report).execute_script_report(filters)
	columns, data = result[0], result[1]
	header, keys = [], []
	for col in columns:
		if isinstance(col, str):  # "Label:Type/Options:Width"
			label = col.split(":")[0]
			header.append(label)
			keys.append(frappe.scrub(label))
		else:
			header.append(col.get("label") or col.get("fieldname"))
			keys.append(col.get("fieldname"))
	rows = [header]
	for row in data:
		if isinstance(row, dict):
			rows.append([_cell(row.get(k)) for k in keys])
		else:
			rows.append([_cell(v) for v in list(row)[: len(keys)]])

	period = f"_{from_date}_to_{to_date}" if from_date else f"_{today()}"
	filename = re.sub(r"[^A-Za-z0-9_-]+", "_", report.replace("Periskope ", "", 1)) + period
	if format == "csv":
		from frappe.utils.csvutils import to_csv

		frappe.response["result"] = to_csv(rows)
		frappe.response["doctype"] = filename
		frappe.response["type"] = "csv"
	else:
		from frappe.utils.xlsxutils import make_xlsx

		frappe.response["filename"] = filename + ".xlsx"
		frappe.response["filecontent"] = make_xlsx(rows, report[:31]).getvalue()
		frappe.response["type"] = "binary"


def _cell(value):
	if value is None:
		return ""
	if isinstance(value, int | float | str):
		return value
	return str(value)


def _system(reports) -> str:
	brand = frappe.get_cached_doc("Periskope Settings").get("brand_name") or "the WhatsApp app"
	listed = "\n".join(f"- {r}: {REPORTS[r][0]}" + ("" if REPORTS[r][1] else " (no period)") for r in reports)
	return SYSTEM.format(
		brand=brand,
		today=frappe.utils.format_date(today(), "EEEE, d MMMM yyyy") + f" ({today()})",
		user=frappe.utils.get_fullname(),
		manager=", a manager" if access.is_manager() else "",
		counts=_counts(),
		numbers=_numbers(),
		test_mode="on" if safety.test_mode() else "off",
		reports=listed or "- none: this user may not download any report.",
		manual=manual(),
	)


def _counts() -> str:
	from periskope_connector.api import inbox

	try:
		c = inbox.counts()
	except Exception:
		return "- not available"
	labels = {
		"all": "Open chats",
		"mine": "Open chats assigned to the user",
		"unassigned": "Open chats with no one assigned",
		"unanswered": "Open chats waiting for a reply",
		"unread": "Chats with unread messages",
		"breached": "Chats past their reply time (SLA)",
		"my_team": "Open chats of the user's team",
	}
	return "\n".join(f"- {label}: {c.get(key, 0)}" for key, label in labels.items())


def _numbers() -> str:
	"""Each number the user may work, grouped by how it is connected (Periskope or Meta's WhatsApp
	API), with its connection state."""
	allowed = access.allowed_numbers()
	groups = {}
	for r in frappe.get_all(
		"WhatsApp Number",
		fields=[
			"name",
			"number",
			"display_name",
			"is_active",
			"source",
			"is_ready",
			"disconnected_since",
			"last_message_on",
			"default_assignee",
			"default_team",
		],
		order_by="number asc",
	):
		if allowed is not None and r.name not in allowed:
			continue
		if not r.is_active:
			state = "turned off in the app"
		elif r.is_ready:
			state = "connected"
		else:
			state = "NOT connected" + (f" since {r.disconnected_since}" if r.disconnected_since else "")
		parts = [f"+{r.number}" + (f" ({r.display_name})" if r.display_name else ""), state]
		if r.default_assignee:
			parts.append(f"its default rep is {frappe.utils.get_fullname(r.default_assignee)}")
		if r.default_team:
			parts.append(f"its team is {r.default_team}")
		if r.last_message_on:
			parts.append(f"last message {r.last_message_on}")
		source = "Meta's WhatsApp API" if r.source == "Meta" else "Periskope"
		groups.setdefault(source, []).append("  - " + ", ".join(parts))
	if not groups:
		return "- none"
	return "\n".join(
		f"- Connected through {source} ({len(rows)}):\n" + "\n".join(rows) for source, rows in groups.items()
	)


def manual() -> str:
	"""The README's user manual, without images, as the AI's knowledge of the app."""

	def read():
		path = os.path.join(os.path.dirname(frappe.get_app_path("periskope_connector")), "README.md")
		try:
			text = open(path, encoding="utf-8").read()
		except OSError:
			return ""
		start = text.find("## User Manual")
		end = text.find("\n## ", start + 5)
		text = text[start:end] if start >= 0 else text
		text = re.sub(r"!\[[^\]]*\]\([^)]*\)\n?", "", text)  # images
		text = re.sub(r"^\*[^*\n]+\.\*\n", "", text, flags=re.M)  # their captions
		text = re.sub(r"\n{3,}", "\n\n", text)
		return text[:MAX_MANUAL]

	import periskope_connector

	return frappe.cache().get_value(f"periskope:help_bot:manual:{periskope_connector.__version__}", read)


def _conversation(history, question) -> str:
	if isinstance(history, str):
		history = frappe.parse_json(history)
	lines = []
	for turn in (history or [])[-MAX_TURNS:]:
		if not isinstance(turn, dict):
			continue
		who = "Assistant" if turn.get("role") == "assistant" else "User"
		text = str(turn.get("text") or "").strip()[: MAX_QUESTION * 2]
		if text:
			lines.append(f"{who}: {text}")
	if lines:
		return "The conversation so far:\n" + "\n\n".join(lines) + f"\n\nThe user's new message:\n{question}"
	return question
