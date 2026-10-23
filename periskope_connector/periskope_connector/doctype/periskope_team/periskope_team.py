"""A team of reps answering some of our WhatsApp numbers.

Each team keeps one Frappe Assignment Rule on Periskope Chat (as Frappe Helpdesk does for its teams):
an open or pending chat of the team with nobody assigned goes to a member, in turn (Round Robin) or to
whoever has the fewest open chats (Load Balancing). "Manual" keeps the rule switched off.
"""

import frappe
from frappe.model.document import Document

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


class PeriskopeTeam(Document):
	def on_update(self):
		self.sync_assignment_rule()

	def on_trash(self):
		if self.assignment_rule and frappe.db.exists("Assignment Rule", self.assignment_rule):
			frappe.delete_doc("Assignment Rule", self.assignment_rule, ignore_permissions=True)

	def member_users(self) -> list[str]:
		return [m.user for m in self.members if m.user]

	def sync_assignment_rule(self):
		name = (
			self.assignment_rule
			if self.assignment_rule and frappe.db.exists("Assignment Rule", self.assignment_rule)
			else None
		)
		rule = frappe.get_doc("Assignment Rule", name) if name else frappe.new_doc("Assignment Rule")
		users = self.member_users()
		rule.update(
			{
				"document_type": "Periskope Chat",
				"description": f"WhatsApp chats of the {self.team_name} team (kept by Periskope Team).",
				"assign_condition": f'team == "{self.name}" and status in ("Open", "Pending")',
				"unassign_condition": "",
				"close_condition": "",
				"rule": "Load Balancing" if self.assignment == "Load Balancing" else "Round Robin",
				"disabled": 1 if self.assignment == "Manual" or not users else 0,
				"priority": 1,
			}
		)
		rule.set("users", [{"user": u} for u in users])
		if not rule.get("assignment_days"):
			rule.set("assignment_days", [{"day": d} for d in DAYS])
		if name:
			rule.save(ignore_permissions=True)
		else:
			# Assignment Rule is named by hand ("Prompt"): name it after the team.
			rule.insert(ignore_permissions=True, set_name=f"WhatsApp Team {self.name}")
			self.db_set("assignment_rule", rule.name, update_modified=False)


def teams_of(user: str) -> list[frappe._dict]:
	"""The teams a user belongs to, with whether members see the team's chats."""
	parents = frappe.get_all(
		"Periskope Team Member", filters={"user": user, "parenttype": "Periskope Team"}, pluck="parent"
	)
	if not parents:
		return []
	return frappe.get_all(
		"Periskope Team", filters={"name": ["in", parents]}, fields=["name", "team_can_see_team_chats"]
	)


def team_for_number(number: str | None) -> str | None:
	"""The team new chats on this number go to: the number's default team, else a team listing it."""
	if not number:
		return None
	team = frappe.db.get_value("WhatsApp Number", number, "default_team")
	if team:
		return team
	return frappe.db.get_value(
		"Periskope Agent Number", {"whatsapp_number": number, "parenttype": "Periskope Team"}, "parent"
	)
