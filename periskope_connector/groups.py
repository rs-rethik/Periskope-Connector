"""Which WhatsApp groups are a customer's.

A group is first taken for a team (Internal) group. It becomes a customer's group when one of its
members is that customer's contact: the member's number is on the customer's lead, deal, contact or
address. Our own people never count, also when their number is saved on a customer:
- a user, an active employee or one of our WhatsApp numbers,
- a number that is a member of OUR_GROUPS or more groups (customers are in one to three),
- a member whose WhatsApp name has our company's name in it ('Ravi Pranera Sales').

When members point to more than one company, the one with the most members wins. When several are
level, the one the group name suggests wins. One person saved on several companies is the newest
of them (a company spelt two ways). Members of different companies with none ahead: the group stays
Internal. A group a manager marked Internal is never changed.
"""

import json
from collections import Counter

import frappe
from frappe import _
from frappe.query_builder.functions import Max

from periskope_connector.client import fetch_members
from periskope_connector.linking import (
	by_organization,
	member_organizations,
	name_key,
	own_companies,
	relink_group,
	relink_messages,
	resembles,
	with_country_code,
)

OUR_GROUPS = 4
MEMBERS_JOB = "periskope_group_members:{}"
GROUP_FIELDS = ["name", "chat_id", "group_name", "group_type", "organization", "crm_lead", "crm_deal"]


# --- Entry points -----------------------------------------------------------------------------------


def on_group_message(chat_id: str, number: str | None, name: str | None = None) -> str | None:
	"""A member wrote in a group. Checks the group with the members already known and this one. The
	first time, the member list is read from Periskope in a job. Never raises: a message must be
	saved even if this fails."""
	group = _open_group(chat_id)
	if not group:
		return None
	members = stored_members(group.name)
	if not members:
		frappe.enqueue(
			"periskope_connector.groups.refresh_and_map",
			chat_id=chat_id,
			job_id=MEMBERS_JOB.format(chat_id),
			deduplicate=True,
			enqueue_after_commit=True,
		)
	if number and number not in dict(members):
		members.append([number, name or ""])
	return map_group(group, members)


def refresh_and_map(chat_id: str) -> str | None:
	"""Job: read a group's members from Periskope, keep them, and check the group again."""
	group = frappe.db.get_value("Periskope Group", {"chat_id": chat_id}, GROUP_FIELDS, as_dict=True)
	if not group:
		return None
	members = refresh_members(group)
	return map_group(group, members) if _is_open(group) else None


def map_groups(limit: int = 500, ask_periskope: bool = False) -> int:
	"""Team groups whose name now matches a customer, or with a customer's contact among their
	members, become that customer's group. With `ask_periskope` every group's member list is read
	again first (a call per group, so only in jobs). Returns how many changed.
	Run with: bench --site <site> execute periskope_connector.groups.map_groups"""
	if ask_periskope:
		for group in frappe.get_all("Periskope Group", fields=GROUP_FIELDS, limit=limit):
			refresh_members(group)
	counts = member_group_counts()
	changed = 0
	for group in frappe.get_all(
		"Periskope Group",
		filters={
			"group_type": "Internal",
			"marked_internal": 0,
			"organization": ["is", "not set"],
			"crm_lead": ["is", "not set"],
			"crm_deal": ["is", "not set"],
		},
		fields=GROUP_FIELDS,
		limit=limit,
	):
		if group.group_name and by_organization(group.group_name):
			frappe.db.set_value("Periskope Group", group.name, "group_type", "Customer")
			relink_messages(group.chat_id)
			changed += 1
		elif map_group(group, stored_members(group.name) or _senders(group.chat_id), counts):
			changed += 1
	return changed


def map_groups_daily() -> int:
	"""Scheduler entry: every group's members read again, and the team groups checked again."""
	try:
		return map_groups(limit=5000, ask_periskope=True)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Periskope: daily group mapping failed")
		return 0


def is_internal(chat_id: str | None) -> bool:
	"""Whether a chat is a team (Internal) group: our own talk, kept out of the CRM."""
	if not (chat_id or "").endswith("@g.us"):
		return False
	return frappe.db.get_value("Periskope Group", {"chat_id": chat_id}, "group_type") == "Internal"


def mark_internal(chat_id: str, user: str | None = None):
	"""A manager's word that a group is a team group: its organization link goes, it is never
	matched again by itself, and what it put in the CRM is removed (in a job)."""
	from periskope_connector.linking import ensure_group

	group = ensure_group(chat_id).name
	frappe.db.set_value(
		"Periskope Group",
		group,
		{
			"group_type": "Internal",
			"marked_internal": 1,
			"organization": None,
			"crm_lead": None,
			"crm_deal": None,
		},
	)
	# the chat rows lose the customer (and "linked by hand"), and the messages are linked again: none
	relink_group(group, user)
	from periskope_connector import workflow

	for name in frappe.get_all("Periskope Chat", filters={"chat_id": chat_id}, pluck="name"):
		workflow.log(name, "Linked", _("Marked as an internal group"), user=user)
	frappe.enqueue(
		"periskope_connector.groups.remove_from_crm",
		chat_id=chat_id,
		queue="long",
		enqueue_after_commit=True,
	)


def remove_from_crm(chat_id: str) -> int:
	"""Take a team group's messages out of the CRM: their timeline comments, the AI's notes on the
	chat, and the links of its messages and chat rows. Returns how many messages were unlinked."""
	message = frappe.qb.DocType("Periskope Message")
	rows = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": chat_id},
		or_filters={"timeline_comment": ["is", "set"], "crm_lead": ["is", "set"], "crm_deal": ["is", "set"]},
		fields=["name", "timeline_comment"],
	)
	for comment in {r.timeline_comment for r in rows if r.timeline_comment}:
		frappe.delete_doc("Comment", comment, ignore_permissions=True, ignore_missing=True)
	if rows:
		(
			frappe.qb.update(message)
			.set(message.crm_lead, None)
			.set(message.crm_deal, None)
			.set(message.timeline_comment, None)
			.set(message.link_status, "Internal")
			.set(message.link_method, "")
			.where(message.name.isin([r.name for r in rows]))
		).run()
	# the chat rows first: their summary note link would stop the note from being deleted
	chat = frappe.qb.DocType("Periskope Chat")
	(
		frappe.qb.update(chat)
		.set(chat.crm_lead, None)
		.set(chat.crm_deal, None)
		.set(chat.summary_note, None)
		.where(chat.chat_id == chat_id)
	).run()
	for record in frappe.get_all(
		"Periskope CRM Record",
		filters={"chat_id": chat_id, "target_doctype": "FCRM Note"},
		fields=["name", "target_name"],
	):
		frappe.delete_doc("Periskope CRM Record", record.name, ignore_permissions=True)  # it links the note
		frappe.delete_doc("FCRM Note", record.target_name, ignore_permissions=True, ignore_missing=True)
	return len(rows)


def remove_internal_groups_from_crm() -> int:
	"""Job: every team group's messages out of the CRM (after this rule came in). Commits per group
	so a failure keeps the groups already done."""
	done = 0
	for chat_id in frappe.get_all("Periskope Group", filters={"group_type": "Internal"}, pluck="chat_id"):
		try:
			done += remove_from_crm(chat_id)
			frappe.db.commit()  # one group at a time: a later failure keeps the earlier ones done
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"Periskope: could not take group {chat_id} out of the CRM")
	return done


# --- Deciding ---------------------------------------------------------------------------------------


def map_group(group, members: list, counts: Counter | None = None) -> str | None:
	"""Link the group to the company its members show, if there is one. Returns it, or None."""
	frappe.db.savepoint("periskope_map_group")
	try:
		organization = choose_organization(group.group_name, members, counts)
		if not organization:
			return None
		from periskope_connector.api.crm_panel import set_group_organization

		set_group_organization(group.chat_id, group.name, organization)
		return organization
	except Exception:
		frappe.db.rollback(save_point="periskope_map_group")
		frappe.log_error(title=f"Periskope: could not map group {group.chat_id} by its members")
		return None


def choose_organization(group_name: str | None, members: list, counts: Counter | None = None) -> str | None:
	"""The company of a group's customer members (see the module docstring), or None. `counts`: in
	how many groups each number is (member_group_counts), when the caller has it already."""
	counts = member_group_counts() if counts is None else counts
	reached = {}  # organization -> the member numbers that point to it
	for number, name in members:
		if is_ours(number, name, counts):
			continue
		for organization in member_organizations(number):
			reached.setdefault(organization, set()).add(with_country_code(number))
	if not reached:
		return None
	most = max(len(numbers) for numbers in reached.values())
	level = sorted(o for o, numbers in reached.items() if len(numbers) == most)
	if len(level) == 1:
		return level[0]
	named = [o for o in level if resembles(o, group_name)]
	if len(named) == 1:
		return named[0]
	candidates = named or level
	if len({frozenset(reached[o]) for o in candidates}) > 1:
		return None  # members of different companies, none ahead: left as a team group
	return newest_organization(candidates)


def is_ours(number: str | None, name: str | None = None, counts: Counter | None = None) -> bool:
	"""Whether a group member is one of our own people (see the module docstring)."""
	full = with_country_code(number)
	if not full:
		return True
	from periskope_connector.linking import staff_numbers

	counts = member_group_counts() if counts is None else counts
	if full in staff_numbers() or counts[full] >= OUR_GROUPS:
		return True
	return bool(_brand_words() & set(name_key(name).split()))


def newest_organization(organizations: list[str]) -> str:
	"""Of the spellings of one company, the one with the newest deal, else the newest lead."""
	names = sorted(organizations)
	newest = {}
	for doctype in ("CRM Deal", "CRM Lead"):
		table = frappe.qb.DocType(doctype)
		newest[doctype] = dict(
			frappe.qb.from_(table)
			.select(table.organization, Max(table.modified))
			.where(table.organization.isin(names))
			.groupby(table.organization)
			.run()
		)
	never = frappe.utils.get_datetime("1900-01-01")
	return max(names, key=lambda o: (newest["CRM Deal"].get(o) or never, newest["CRM Lead"].get(o) or never))


# --- Members ----------------------------------------------------------------------------------------


def refresh_members(group) -> list:
	"""Read a group's members from Periskope and keep them on the group. Returns [[number, name]].
	Keeps the old list when Periskope gives none (not reachable)."""
	members = fetch_members(group.chat_id, _phone_of(group.chat_id))
	if members:
		frappe.db.set_value(
			"Periskope Group", group.name, "members", json.dumps(members), update_modified=False
		)
		return members
	return stored_members(group.name)


def stored_members(group: str) -> list:
	"""The member list kept on the group: [[number, name]], or []."""
	return _parse(frappe.db.get_value("Periskope Group", group, "members"))


def member_group_counts() -> Counter:
	"""In how many groups each number is a member."""
	counts = Counter()
	for value in frappe.get_all("Periskope Group", filters={"members": ["is", "set"]}, pluck="members"):
		counts.update({with_country_code(number) for number, _name in _parse(value)})
	return counts


# --- Helpers ----------------------------------------------------------------------------------------


def _open_group(chat_id: str):
	group = frappe.db.get_value("Periskope Group", {"chat_id": chat_id}, GROUP_FIELDS, as_dict=True)
	return group if group and _is_open(group) else None


def _is_open(group) -> bool:
	"""A team group nobody linked or marked: the only kind that is matched automatically."""
	if group.group_type != "Internal" or group.organization or group.crm_lead or group.crm_deal:
		return False
	return not frappe.db.get_value("Periskope Group", group.name, "marked_internal")


def _senders(chat_id: str) -> list:
	rows = frappe.get_all(
		"Periskope Message",
		filters={"chat_id": chat_id, "direction": "Incoming", "sender_phone": ["is", "set"]},
		fields=["sender_phone", "sender_name"],
		distinct=True,
		limit=50,
	)
	return [[r.sender_phone, r.sender_name or ""] for r in rows]


def _phone_of(chat_id: str) -> str | None:
	"""One of our Periskope numbers in the group, to read it through."""
	from periskope_connector import meta

	phones = frappe.get_all(
		"Periskope Chat", filters={"chat_id": chat_id}, pluck="whatsapp_number", order_by="modified desc"
	)
	return next((p for p in phones if p and not meta.is_meta(p)), None)


def _brand_words() -> set[str]:
	"""The first word of each of our company names ('pranera'), when long enough to be telling."""
	words = (name_key(company).split() for company in own_companies())
	return {w[0] for w in words if w and len(w[0]) >= 4}


def _parse(value: str | None) -> list:
	try:
		rows = json.loads(value or "[]")
	except ValueError:
		return []
	return [[str(r[0]), str(r[1] or "")] for r in rows if isinstance(r, list) and r and r[0]]
