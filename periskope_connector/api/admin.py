"""Admin Settings of the WhatsApp inbox: everything managers and admins set up, without Desk.

- Records (KINDS): teams, WhatsApp numbers, agents, SLA policies, shared quick replies and labels. Each
  kind lists the fields the inbox may show and change; the field definitions come from the doctype, so
  the inbox draws the forms itself. Teams, numbers, agents, SLA and quick replies are for managers;
  labels for the admins.
- Periskope Settings (SECTIONS), for the admins: saved through the settings document, so its own
  checks apply. Keys are write-only: the inbox is told only whether one is set.
"""

import html
import json

import frappe
from frappe import _
from frappe.utils import cint, strip_html

from periskope_connector import access
from periskope_connector.api.permission import ADMIN_ROLES, require_admin, require_manager

MANAGER, ADMIN = "manager", "admin"

KINDS = {
	"teams": {
		"doctype": "Periskope Team",
		"role": MANAGER,
		"fields": ["team_name", "assignment", "team_can_see_team_chats", "members", "numbers"],
		"list": ["team_name", "assignment", "team_can_see_team_chats"],
		"order_by": "team_name asc",
	},
	"numbers": {
		"doctype": "WhatsApp Number",
		"role": MANAGER,
		"fields": ["display_name", "is_active", "default_team", "default_assignee", "sales_person"],
		"list": [
			"number",
			"display_name",
			"is_active",
			"default_team",
			"default_assignee",
			"wa_state",
			"is_ready",
			"phone_name",
			"last_sync_on",
			"last_message_on",
			"disconnected_since",
		],
		"order_by": "number asc",
		"create": False,
		"delete": False,
	},
	"agents": {
		"doctype": "Periskope Agent",
		"role": MANAGER,
		"fields": ["user", "is_active", "numbers"],
		"list": ["user", "agent_name", "is_active"],
		"order_by": "agent_name asc",
	},
	"sla": {
		"doctype": "Periskope SLA Policy",
		"role": MANAGER,
		"fields": [
			"policy_name",
			"enabled",
			"is_default",
			"priority",
			"first_response_minutes",
			"resolution_minutes",
			"use_working_hours",
			"numbers",
			"teams",
		],
		"list": ["policy_name", "enabled", "is_default", "first_response_minutes", "resolution_minutes"],
		"order_by": "priority desc, policy_name asc",
	},
	"quick_replies": {
		"doctype": "Periskope Canned Response",
		"role": MANAGER,
		"fields": ["title", "body", "is_shared"],
		"list": ["title", "body", "is_shared", "owner"],
		"order_by": "title asc",
	},
	"labels": {
		"doctype": "Periskope Tag",
		"role": ADMIN,
		"fields": ["tag_name", "active", "description", "sort_order"],
		"list": ["tag_name", "active", "description", "sort_order"],
		"order_by": "sort_order asc, tag_name asc",
		"delete": False,  # messages keep their tag; switch a label off instead
	},
}
# The doctypes a form may pick a value from (Link fields and the rows of a Table MultiSelect).
PICKABLE = {"User", "WhatsApp Number", "Periskope Team", "Sales Person", "Holiday List"}

SECTIONS = [
	{
		"key": "connect",
		"title": "Periskope connection",
		"confirm": True,
		"fields": [
			"enabled",
			"api_key",
			"base_url",
			"connection_status",
			"last_checked",
			"webhook_url",
			"webhook_signing_secret",
			"webhook_events",
		],
	},
	{
		"key": "ai",
		"title": "AI",
		"fields": [
			"ai_provider",
			"anthropic_api_key",
			"ai_model",
			"ai_base_url",
			"ai_monthly_budget_tokens",
			"ai_status",
			"ai_business_context",
		],
	},
	{"key": "voice", "title": "Voice notes", "fields": ["gemini_api_key", "gemini_model"]},
	{
		"key": "team",
		"title": "Approvers and alerts",
		"fields": ["approver_emails", "alert_emails", "escalation_emails", "default_rep"],
	},
	{
		"key": "auto_reply",
		"title": "Auto reply and working hours",
		"fields": [
			"enable_auto_reply",
			"reply_delay_minutes",
			"working_hours_start",
			"working_hours_end",
			"working_days",
			"holiday_list",
		],
	},
	{
		"key": "test_mode",
		"title": "Test mode",
		"confirm": True,
		"fields": ["test_mode", "test_allowed_chats", "test_allowed_emails"],
	},
]
SECRET_TYPES = ("Password",)


def is_admin(user: str | None = None) -> bool:
	user = user or frappe.session.user
	return user == "Administrator" or bool(set(ADMIN_ROLES) & set(frappe.get_roles(user)))


def _require(role: str):
	"""Managers or admins only. Checked here as well as by require_manager / require_admin, which
	frappe.only_for skips while tests run."""
	(require_admin if role == ADMIN else require_manager)()
	allowed = is_admin() if role == ADMIN else access.is_manager()
	if not allowed:
		frappe.throw(
			_("Only {0} can change these settings.").format(_("admins") if role == ADMIN else _("managers")),
			frappe.PermissionError,
		)


def _kind(kind: str) -> dict:
	cfg = KINDS.get(kind)
	if not cfg:
		frappe.throw(_("Unknown settings page {0}").format(kind))
	_require(cfg["role"])
	return cfg


def _field_meta(doctype: str, fieldnames: list[str]) -> list[dict]:
	meta = frappe.get_meta(doctype)
	out = []
	for name in fieldnames:
		df = meta.get_field(name)
		if not df:
			continue
		row = {
			"fieldname": df.fieldname,
			"label": _(df.label),
			"fieldtype": df.fieldtype,
			"options": df.options,
			# Descriptions are written for the Desk form, some with HTML: plain text here.
			"description": html.unescape(strip_html(_(df.description))) if df.description else "",
			"reqd": df.reqd,
			"read_only": df.read_only,
			"default": df.default,
		}
		if df.fieldtype in ("Table MultiSelect", "Table"):
			child = frappe.get_meta(df.options)
			link = next((f for f in child.fields if f.fieldtype in ("Link", "Select")), None)
			row["child"] = {
				"doctype": df.options,
				"field": link.fieldname,
				"options": link.options,
				"fieldtype": link.fieldtype,
			}
		out.append(row)
	return out


@frappe.whitelist()
def get_meta(kind: str) -> dict:
	access.require_inbox_user()
	cfg = _kind(kind)
	return {
		"doctype": cfg["doctype"],
		"fields": _field_meta(cfg["doctype"], cfg["fields"]),
		"list": _field_meta(cfg["doctype"], cfg["list"]),
		"can_create": cfg.get("create", True),
		"can_delete": cfg.get("delete", True),
	}


def _tables(doctype: str, fields: list[str]) -> dict:
	meta = frappe.get_meta(doctype)
	out = {}
	for name in fields:
		df = meta.get_field(name)
		if df and df.fieldtype in ("Table MultiSelect", "Table"):
			child = frappe.get_meta(df.options)
			link = next(f for f in child.fields if f.fieldtype in ("Link", "Select"))
			out[name] = (df.options, link.fieldname)
	return out


def _as_dict(doc, cfg) -> dict:
	tables = _tables(cfg["doctype"], cfg["fields"])
	row = {"name": doc.name}
	for field in dict.fromkeys(cfg["list"] + cfg["fields"]):
		if field in tables:
			row[field] = [r.get(tables[field][1]) for r in doc.get(field) or []]
		else:
			row[field] = doc.get(field)
	return row


@frappe.whitelist()
def list_records(kind: str) -> list[dict]:
	access.require_inbox_user()
	cfg = _kind(kind)
	names = frappe.get_all(cfg["doctype"], pluck="name", order_by=cfg["order_by"], limit=500)
	return [_as_dict(frappe.get_doc(cfg["doctype"], n), cfg) for n in names]


@frappe.whitelist(methods=["POST"])
def save_record(kind: str, values: str | dict, name: str | None = None) -> dict:
	"""Create or change one record. Only the kind's own fields are taken; tables come as lists."""
	access.require_inbox_user()
	cfg = _kind(kind)
	values = json.loads(values) if isinstance(values, str) else (values or {})
	if not name and not cfg.get("create", True):
		frappe.throw(_("These are added from Periskope, not by hand."))
	doc = frappe.get_doc(cfg["doctype"], name) if name else frappe.new_doc(cfg["doctype"])
	tables = _tables(cfg["doctype"], cfg["fields"])
	autoname = frappe.get_meta(cfg["doctype"]).autoname or ""
	name_field = autoname[len("field:") :] if autoname.startswith("field:") else None
	for field in cfg["fields"]:
		if field not in values:
			continue
		if field in tables:
			_child, link = tables[field]
			doc.set(field, [{link: v} for v in dict.fromkeys(values[field] or []) if v])
		elif name and field == name_field:
			continue  # a record named by this field is not renamed here
		else:
			doc.set(field, values[field])
	doc.flags.ignore_permissions = True  # checked above: managers or admins only
	doc.save() if name else doc.insert()
	return _as_dict(doc, cfg)


@frappe.whitelist(methods=["POST"])
def delete_record(kind: str, name: str) -> None:
	access.require_inbox_user()
	cfg = _kind(kind)
	if not cfg.get("delete", True):
		frappe.throw(_("This cannot be deleted here."))
	frappe.delete_doc(cfg["doctype"], name, ignore_permissions=True)


@frappe.whitelist()
def pick(doctype: str, query: str = "") -> list[dict]:
	"""Values to choose from in a form: users, numbers, teams, sales persons, holiday lists."""
	access.require_inbox_user()
	_require(MANAGER)
	if doctype not in PICKABLE or not frappe.db.exists("DocType", doctype):
		frappe.throw(_("Cannot pick from {0}").format(doctype))
	like = f"%{(query or '').strip()}%"
	if doctype == "User":
		return frappe.get_all(
			"User",
			filters={
				"enabled": 1,
				"user_type": "System User",
				"name": ["not in", ["Administrator", "Guest"]],
			},
			or_filters=[["name", "like", like], ["full_name", "like", like]],
			fields=["name as value", "full_name as label"],
			order_by="full_name asc",
			# a search shows the best 50; the full list (up to 500) names the users already chosen
			limit=50 if (query or "").strip() else 500,
		)
	title = {"WhatsApp Number": "display_name"}.get(doctype)
	fields = ["name as value", f"{title} as label"] if title else ["name as value", "name as label"]
	return frappe.get_all(
		doctype,
		filters={"name": ["like", like]} if query else {},
		fields=fields,
		order_by="name asc",
		limit=100,
	)


@frappe.whitelist(methods=["POST"])
def invite_agent(
	email: str, first_name: str, numbers: str | list | None = None, send_welcome_email: int = 0
) -> dict:
	"""A new sales user: a CRM user with the Sales User role (no Desk needed) and an agent on the
	chosen numbers. An existing user only gets the role and the agent record."""
	access.require_inbox_user()
	_require(MANAGER)
	email = (email or "").strip().lower()
	if not frappe.utils.validate_email_address(email):
		frappe.throw(_("Enter a valid email address."))
	if not frappe.db.exists("User", email):
		user = frappe.get_doc(
			{
				"doctype": "User",
				"email": email,
				"first_name": (first_name or email.split("@")[0]).strip(),
				"send_welcome_email": 1 if cint(send_welcome_email) else 0,
			}
		)
		user.flags.ignore_permissions = True
		user.insert()
	user = frappe.get_doc("User", email)
	user.flags.ignore_permissions = True  # a manager may add sales users (checked above)
	user.add_roles("Sales User")
	numbers = json.loads(numbers) if isinstance(numbers, str) else (numbers or [])
	agent = frappe.db.get_value("Periskope Agent", {"user": email})
	values = {"user": email, "is_active": 1, "numbers": numbers}
	return save_record("agents", values, name=agent)


# --- Periskope Settings --------------------------------------------------------------------------


def _section(key: str) -> dict:
	section = next((s for s in SECTIONS if s["key"] == key), None)
	if not section:
		frappe.throw(_("Unknown settings section {0}").format(key))
	return section


@frappe.whitelist()
def get_settings() -> dict:
	"""Every section with its fields and values (keys only as "set" or not)."""
	access.require_inbox_user()
	_require(ADMIN)
	settings = frappe.get_single("Periskope Settings")
	from periskope_connector.ai.classifier import available_providers
	from periskope_connector.setup_status import get_status

	sections = []
	for s in SECTIONS:
		fields = _field_meta("Periskope Settings", s["fields"])
		for f in fields:
			if f["fieldtype"] in SECRET_TYPES:
				f["is_set"] = bool(settings.get_password(f["fieldname"], raise_exception=False))
				f["value"] = ""
			elif f["fieldtype"] == "Table":
				f["value"] = [r.get(f["child"]["field"]) for r in settings.get(f["fieldname"]) or []]
			else:
				f["value"] = settings.get(f["fieldname"])
			if f["fieldname"] == "ai_provider":
				f["options"] = "\n".join(available_providers())
		sections.append({**{k: v for k, v in s.items() if k != "fields"}, "fields": fields})
	return {"sections": sections, "setup": get_status()}


@frappe.whitelist(methods=["POST"])
def save_settings(section: str, values: str | dict) -> dict:
	"""Save one section. A key is changed only when a new one is typed in."""
	access.require_inbox_user()
	_require(ADMIN)
	s = _section(section)
	values = json.loads(values) if isinstance(values, str) else (values or {})
	settings = frappe.get_single("Periskope Settings")
	meta = frappe.get_meta("Periskope Settings")
	for name in s["fields"]:
		if name not in values:
			continue
		df = meta.get_field(name)
		if not df or df.read_only:
			continue
		if df.fieldtype in SECRET_TYPES:
			if values[name]:
				settings.set(name, values[name])
		elif df.fieldtype == "Table":
			child = frappe.get_meta(df.options)
			link = next(f for f in child.fields if f.fieldtype in ("Link", "Select"))
			settings.set(name, [{link.fieldname: v} for v in dict.fromkeys(values[name] or []) if v])
		else:
			settings.set(name, values[name])
	settings.flags.ignore_permissions = True
	settings.save()
	return get_settings()


@frappe.whitelist(methods=["POST"])
def run_action(action: str) -> dict:
	"""The buttons of the settings: test the connection, sync the numbers, register the webhook,
	test the AI. Each keeps its own admin check (api/settings.py)."""
	access.require_inbox_user()
	_require(ADMIN)
	from periskope_connector.api import settings as settings_api

	actions = {
		"test_connection": settings_api.test_connection,
		"sync_numbers": settings_api.sync_phones,
		"setup_webhooks": settings_api.setup_webhooks,
		"test_ai": settings_api.test_ai_connection,
	}
	if action not in actions:
		frappe.throw(_("Unknown action {0}").format(action))
	result = actions[action]()
	return {"result": result if isinstance(result, dict) else {"count": len(result or [])}}


@frappe.whitelist()
def my_access() -> dict:
	"""Which pages of the settings the user may open."""
	access.require_inbox_user()
	return {"manager": access.is_manager(), "admin": is_admin()}
