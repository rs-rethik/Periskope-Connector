"""Roles and checks stay consistent: each doctype grants what api/permission.py describes, and every
whitelisted method starts with a role check (or authenticates itself, for the two guest endpoints)."""

import ast
import json
from pathlib import Path

from frappe.tests.utils import FrappeTestCase

APP = Path(__file__).resolve().parents[1]
DOCTYPES = APP / "periskope_connector" / "doctype"

CONFIG = (
	"Periskope Tag",
	"Periskope Knowledge Entry",
	"Periskope Suggestion",
	"Periskope Chat",
)
QUEUE = ("Periskope Contact", "Periskope Group")
LOGS = (
	"Periskope Message",
	"Periskope Auto Reply",
	"Periskope Agent Run",
	"Periskope Agent Action",
	"Periskope AI Usage",
	"Periskope Webhook Log",
	"Periskope Reply Reminder",
	"Periskope CRM Record",
)
CHECKS = {"require_admin", "require_manager"}
SELF_AUTHENTICATED = {("api/webhook.py", "receive"), ("api/suggestions.py", "decide")}


def _perms(doctype: str) -> dict[str, dict]:
	folder = doctype.lower().replace(" ", "_")
	data = json.loads((DOCTYPES / folder / f"{folder}.json").read_text())
	return {p["role"]: p for p in data["permissions"]}


class TestRoleMatrix(FrappeTestCase):
	def test_admins_manage_the_setup_and_sales_managers_read_it(self):
		for doctype in (*CONFIG, *QUEUE, "Periskope Settings"):
			perms = _perms(doctype)
			for role in ("System Manager", "WhatsApp Manager"):
				self.assertTrue(perms[role].get("write"), f"{role} cannot change {doctype}")
			self.assertTrue(perms["Sales Manager"].get("read"), f"Sales Manager cannot read {doctype}")
		for doctype in CONFIG:
			self.assertFalse(_perms(doctype)["Sales Manager"].get("write"), doctype)

	def test_logs_are_read_only_for_everyone(self):
		for doctype in LOGS:
			perms = _perms(doctype)
			for role in ("System Manager", "WhatsApp Manager", "Sales Manager"):
				self.assertTrue(perms[role].get("read"), f"{role} cannot read {doctype}")
				self.assertFalse(perms[role].get("write"), f"{role} can edit {doctype}")

	def test_sales_users_get_nothing_in_desk(self):
		for folder in DOCTYPES.iterdir():
			path = folder / f"{folder.name}.json"
			if path.exists():
				roles = {p["role"] for p in json.loads(path.read_text()).get("permissions", [])}
				self.assertNotIn("Sales User", roles, folder.name)


class TestEveryEndpointChecks(FrappeTestCase):
	def test_each_whitelisted_method_starts_with_a_role_check(self):
		for path in APP.rglob("*.py"):
			rel = str(path.relative_to(APP))
			if "__pycache__" in rel or path.name.startswith("test_") or rel.startswith("tests/"):
				continue
			for node in ast.walk(ast.parse(path.read_text())):
				if not isinstance(node, ast.FunctionDef):
					continue
				if not any("whitelist" in ast.unparse(d) for d in node.decorator_list):
					continue
				if (rel, node.name) in SELF_AUTHENTICATED:
					continue
				called = {
					ast.unparse(n.func).split(".")[-1] for n in ast.walk(node) if isinstance(n, ast.Call)
				}
				self.assertTrue(called & CHECKS, f"{rel}:{node.name} has no role check")
