"""The WhatsApp inbox (the /whatsapp app): chats, messages and counts, read from our own tables.

Every method starts with access.require_inbox_user(), and every query is limited by
access.chat_condition(), the same rule the permission hooks apply to Periskope Chat and Periskope
Message. Nothing here calls Periskope: the browser never gets a Periskope key or media link.
"""

import json
import mimetypes
import re

import frappe
from frappe import _
from frappe.query_builder import Criterion
from frappe.utils import cint

from periskope_connector import access, attachments, branding, meta, numbers, safety, shared_items
from periskope_connector.ai import festival

PAGE = 50
MESSAGE_PAGE = 50
SORTS = {
	"last_message_at": "c.last_message_at",
	"chat_name": "c.chat_name",
	"status": "c.status",
	"message_count": "c.message_count",
	"creation": "c.creation",
	"team": "c.team",
	"response_due": "c.response_due",
	"waiting_since": "c.waiting_since",
}
STATUSES = ("Open", "Pending", "Resolved", "Closed")
CHAT_COLUMNS = """c.name, c.chat_id, c.chat_name, c.is_group, c.contact_number, c.whatsapp_number,
	c.status, c.last_message_at, c.last_message_preview, c.last_message_direction, c.last_incoming_at,
	c.message_count, c.crm_lead, c.crm_deal, c.organization, c._assign, c.team, c.waiting_since, c.read_at,
	c.response_due, c.opened_on, c.resolution_due, c.resolved_on, c.response_breached, c.ai_off, c.no_broadcasts,
	(select m.ai_category from `tabPeriskope Message` m where m.periskope_chat = c.name
		and ifnull(m.ai_category, '') != '' order by m.message_timestamp desc limit 1) as ai_tag"""
MESSAGE_FIELDS = [
	"name",
	"message_id",
	"direction",
	"message_type",
	"body",
	"message_timestamp",
	"sender_phone",
	"sender_name",
	"performed_by",
	"agent_reply",
	"ack",
	"quoted_message_id",
	"media_filename",
	"media_mimetype",
	"media_size",
	"shared_details",
	"media_path",
	"attachment_file",
	"attachment_summary",
	"transcript",
	"is_deleted",
	"edited_on",
	"ai_category",
	"severity",
	"whatsapp_number",
	"sent_by",
	"send_status",
	"send_error",
	"reactions",
	"reply_to",
]


def _parse(value):
	if isinstance(value, str) and value.strip().startswith(("{", "[")):
		return json.loads(value)
	return value


UNREAD = """(c.last_incoming_at is not null and c.last_message_direction = 'Incoming'
	and (c.read_at is null or c.read_at < c.last_incoming_at))"""


def _where(filters: dict, condition: str | None = None) -> tuple[list[str], dict]:
	"""SQL conditions and values for the chat list filters. Unknown keys are ignored. `condition` is
	the user's access condition when the caller already has it (counts builds it once)."""
	user = frappe.session.user
	conditions, values = [], {"user": user, "assign": access.assigned_like(user)}
	if condition is None:
		condition = access.chat_condition(user, "c")
	if condition:
		conditions.append(condition)
	if filters.get("number"):
		conditions.append("c.whatsapp_number = %(number)s")
		values["number"] = filters["number"]
	if filters.get("status") in STATUSES:
		conditions.append("c.status = %(status)s")
		values["status"] = filters["status"]
	elif filters.get("status") == "active":
		conditions.append("c.status in ('Open', 'Pending')")
	if filters.get("mine"):
		conditions.append("c._assign like %(assign)s")
	if filters.get("unassigned"):
		conditions.append("(ifnull(c._assign, '') in ('', '[]'))")
	if filters.get("unanswered"):
		conditions.append("c.last_message_direction = 'Incoming'")
	if filters.get("unread"):
		# read for everyone once its own reader opened it (read_state.py); our reply means it was seen
		conditions.append(UNREAD)
	# a group by its flag or by its WhatsApp id (some older rows lack the flag)
	is_group = "(c.is_group = 1 or c.chat_id like '%%@g.us')"
	if filters.get("groups") is not None and filters.get("groups") != "":
		conditions.append(is_group if cint(filters["groups"]) else f"not {is_group}")
	# internal (team) groups have their own tab; the other tabs leave them out
	internal = """exists (select 1 from `tabPeriskope Group` g where g.chat_id = c.chat_id
		and g.group_type = 'Internal')"""
	if filters.get("internal") is not None and filters.get("internal") != "":
		conditions.append(f"{is_group} and {internal}" if cint(filters["internal"]) else f"not {internal}")
	if filters.get("linked") is not None and filters.get("linked") != "":
		linked = "(ifnull(c.crm_lead, '') != '' or ifnull(c.crm_deal, '') != '')"
		conditions.append(linked if cint(filters["linked"]) else f"not {linked}")
	if filters.get("team"):
		conditions.append("c.team = %(team)s")
		values["team"] = filters["team"]
	if filters.get("my_team"):
		from periskope_connector.periskope_connector.doctype.periskope_team.periskope_team import teams_of

		mine = [t.name for t in teams_of(user)]
		conditions.append(f"c.team in ({', '.join(frappe.db.escape(t) for t in mine)})" if mine else "1=0")
	if filters.get("assigned_to"):
		conditions.append("c._assign like %(assigned_to)s")
		values["assigned_to"] = access.assigned_like(filters["assigned_to"])
	if filters.get("label"):
		conditions.append(
			"""exists (select 1 from `tabPeriskope Chat Label` l where l.parent = c.name
			and l.parenttype = 'Periskope Chat' and l.tag = %(label)s)"""
		)
		values["label"] = filters["label"]
	if filters.get("sla") == "breached":
		from periskope_connector.sla import breached_condition

		conditions.append(breached_condition("c"))
		values["sla_now"] = frappe.utils.now_datetime()
	if filters.get("tag"):
		conditions.append(
			"""exists (select 1 from `tabPeriskope Message` t where t.periskope_chat = c.name
			and t.ai_category = %(tag)s)"""
		)
		values["tag"] = filters["tag"]
	search = (filters.get("search") or "").strip()
	if search:
		values["search"] = f"%{search}%"
		conditions.append(
			"""(c.chat_name like %(search)s or c.contact_number like %(search)s
			or c.last_message_preview like %(search)s or c.chat_id like %(search)s)"""
		)
	return conditions, values


@frappe.whitelist()
def get_chats(
	filters: str | dict | None = None,
	order_by: str = "last_message_at",
	order: str = "desc",
	start: int = 0,
	page_length: int = PAGE,
) -> dict:
	"""One page of the chats the user may see, newest first by default."""
	access.require_inbox_user()
	filters = _parse(filters) or {}
	conditions, values = _where(filters)
	where = " and ".join(conditions) or "1=1"
	sort = SORTS.get(order_by, SORTS["last_message_at"])
	direction = "asc" if (order or "").lower() == "asc" else "desc"
	values.update(start=cint(start), length=min(cint(page_length) or PAGE, 200))
	rows = frappe.db.sql(
		f"""select {CHAT_COLUMNS} from `tabPeriskope Chat` c where {where}
		order by {sort} {direction}, c.name desc limit %(start)s, %(length)s""",
		values,
		as_dict=True,
	)
	total = frappe.db.sql(f"select count(*) from `tabPeriskope Chat` c where {where}", values)[0][0]
	return {"chats": _decorate(rows), "total": total, "start": cint(start)}


def _decorate(rows: list[dict]) -> list[dict]:
	"""Add what the list shows beside the chat fields: unread, assignees, the number's name and the
	lead or deal's title."""
	if not rows:
		return rows
	for r in rows:
		# some older rows lack the flag; a WhatsApp group id says it all
		r.is_group = 1 if r.is_group or (r.chat_id or "").endswith("@g.us") else 0
	names = [r.name for r in rows]
	rows_of_numbers = numbers.all_numbers()
	labels = {n.name: n.display_name or n.number for n in rows_of_numbers}
	sources = {n.name: n.source or "Periskope" for n in rows_of_numbers}
	chat_labels = {}
	for row in frappe.get_all(
		"Periskope Chat Label",
		filters={"parent": ["in", names], "parenttype": "Periskope Chat"},
		fields=["parent", "tag"],
		order_by="idx asc",
	):
		chat_labels.setdefault(row.parent, []).append(row.tag)
	from periskope_connector import sla

	now = frappe.utils.now_datetime()
	leads = _titles("CRM Lead", [r.crm_lead for r in rows if r.crm_lead], "lead_name")
	deals = _titles("CRM Deal", [r.crm_deal for r in rows if r.crm_deal], "organization")
	for row in rows:
		row.unread = bool(
			row.last_incoming_at
			and row.last_message_direction == "Incoming"
			and (not row.read_at or row.read_at < row.last_incoming_at)
		)
		row.assigned_to = json.loads(row.pop("_assign") or "[]")
		row.number_label = labels.get(row.whatsapp_number, row.whatsapp_number or "")
		row.lead_title = leads.get(row.crm_lead) if row.crm_lead else None
		row.deal_title = deals.get(row.crm_deal) if row.crm_deal else None
		row.title = row.chat_name or (f"+{row.contact_number}" if row.contact_number else row.chat_id)
		row.labels = chat_labels.get(row.name, [])
		row.source = sources.get(row.whatsapp_number, "Periskope")
		if row.source == meta.SOURCE:
			# Meta takes free text only within 24 hours of the customer's last message
			closes = row.last_incoming_at and frappe.utils.add_to_date(
				row.last_incoming_at, hours=meta.WINDOW_HOURS
			)
			row.window = {"open": bool(closes and closes > now), "closes_at": str(closes) if closes else None}
		row.update(sla.state(row, now))
	return rows


def _titles(doctype: str, names: list[str], field: str) -> dict:
	if not names:
		return {}
	return dict(
		frappe.get_all(
			doctype, filters={"name": ["in", list(set(names))]}, fields=["name", field], as_list=True
		)
	)


@frappe.whitelist()
def counts() -> dict:
	"""The sidebar badges: chats per quick filter, and unread chats per number. One query for all the
	badges, with the user's access condition built once."""
	access.require_inbox_user()
	condition = access.chat_condition(frappe.session.user, "c")
	parts, values = [], {}
	for key, flt in COUNT_FILTERS.items():
		conditions, where_values = _where(flt, condition)
		values.update(where_values)
		# the access condition is the outer where; each badge adds its own filters
		own = [c for c in conditions if c != condition] or ["1=1"]
		parts.append(f"sum(case when {' and '.join(own)} then 1 else 0 end) as `{key}`")
	where = condition or "1=1"
	row = frappe.db.sql(
		f"select {', '.join(parts)} from `tabPeriskope Chat` c where {where}", values, as_dict=True
	)[0]
	out = {key: int(row.get(key) or 0) for key in COUNT_FILTERS}
	conditions, values = _where({"unread": 1}, condition)
	out["unread_by_number"] = dict(
		frappe.db.sql(
			f"""select c.whatsapp_number, count(*) from `tabPeriskope Chat` c
			where {" and ".join(conditions)} group by c.whatsapp_number""",
			values,
		)
	)
	return out


COUNT_FILTERS = {
	"all": {"status": "active"},
	"mine": {"status": "active", "mine": 1},
	"unassigned": {"status": "active", "unassigned": 1},
	"unanswered": {"status": "active", "unanswered": 1},
	"unread": {"unread": 1},
	"breached": {"sla": "breached"},
	"my_team": {"status": "active", "my_team": 1},
}


def _chat_or_403(chat: str):
	if not access.can_see_chat(chat):
		frappe.throw(_("You cannot open this chat."), frappe.PermissionError)


@frappe.whitelist()
def get_chat(chat: str) -> dict:
	access.require_inbox_user()
	_chat_or_403(chat)
	conditions = "c.name = %(chat)s"
	row = frappe.db.sql(
		f"select {CHAT_COLUMNS} from `tabPeriskope Chat` c where {conditions}", {"chat": chat}, as_dict=True
	)
	return _decorate(row)[0]


@frappe.whitelist()
def get_messages(chat: str, before: str | None = None, limit: int = MESSAGE_PAGE) -> dict:
	"""A page of a chat's messages, oldest first; `before` (a timestamp) loads older ones."""
	access.require_inbox_user()
	_chat_or_403(chat)
	filters = {"periskope_chat": chat}
	if before:
		filters["message_timestamp"] = ["<", before]
	limit = min(cint(limit) or MESSAGE_PAGE, 200)
	rows = frappe.get_all(
		"Periskope Message",
		filters=filters,
		fields=MESSAGE_FIELDS,
		order_by="message_timestamp desc, creation desc",
		limit=limit + 1,
	)
	more = len(rows) > limit
	if more:
		rows, more = _whole_seconds(chat, rows[:limit], rows[limit])
	rows = list(reversed(rows))
	names = _participant_names([r.sender_phone for r in rows if r.sender_phone])
	quoted = _quoted(chat, [r.quoted_message_id for r in rows if r.quoted_message_id])
	senders = {u: frappe.utils.get_fullname(u) for u in {r.sent_by for r in rows if r.sent_by}}
	for row in rows:
		row.has_media = attachments.has_file(row) or bool(row.attachment_file)
		row.pop("media_path", None)  # Periskope's link never goes to the browser
		if row.agent_reply:
			row.sender_label = _("AI assistant")
		elif row.sent_by:
			row.sender_label = senders.get(row.sent_by)
		else:
			row.sender_label = names.get(row.sender_phone) or row.sender_name or ""
		row.quoted = quoted.get(row.quoted_message_id) if row.quoted_message_id else None
		row.reactions = json.loads(row.reactions) if row.reactions else {}
		row.shared = shared_items.from_row(row)
	_deals_made(rows)
	_orders_made(rows)
	return {
		"messages": rows,
		"has_more": more,
		"notes": _notes(chat, rows, more, before),
		"calls": _calls(chat, rows, more, before),
	}


SAME_SECOND_CAP = 500


def _whole_seconds(chat: str, page: list, next_row) -> tuple[list, bool]:
	"""(page, has_more) for a page that never ends inside one second. Message times are whole
	seconds and the next page is asked for as "before <the oldest time shown>": messages sharing
	that second with the oldest one shown would otherwise never be loaded. So the page takes them
	all (newest first, like `page`)."""
	boundary = page[-1].message_timestamp
	if not boundary or next_row.message_timestamp != boundary:
		return page, True
	shown = [r.name for r in page if r.message_timestamp == boundary]
	page += frappe.get_all(
		"Periskope Message",
		filters={"periskope_chat": chat, "message_timestamp": boundary, "name": ["not in", shown]},
		fields=MESSAGE_FIELDS,
		order_by="creation desc",
		limit=SAME_SECOND_CAP,
	)
	more = bool(
		frappe.db.exists("Periskope Message", {"periskope_chat": chat, "message_timestamp": ["<", boundary]})
	)
	return page, more


def _deals_made(rows: list) -> None:
	"""Mark the messages a deal was made from (CRM Deal.periskope_message) with that deal."""
	if not rows or not frappe.get_meta("CRM Deal").has_field("periskope_message"):
		return
	made = frappe.get_all(
		"CRM Deal",
		filters={"periskope_message": ["in", [r.name for r in rows]]},
		fields=["name", "periskope_message"],
	)
	by_message = {}
	for d in made:
		by_message.setdefault(d.periskope_message, []).append(d.name)
	for row in rows:
		row.deals = by_message.get(row.name, [])


def _orders_made(rows: list) -> None:
	"""Mark the messages a Sales Order was made from (Sales Order.periskope_message) with it."""
	if not rows or not frappe.db.exists("DocType", "Sales Order"):
		return
	if not frappe.get_meta("Sales Order").has_field("periskope_message"):
		return
	made = frappe.get_all(
		"Sales Order",
		filters={"periskope_message": ["in", [r.name for r in rows]], "docstatus": ["<", 2]},
		fields=["name", "periskope_message"],
	)
	by_message = {}
	for o in made:
		by_message.setdefault(o.periskope_message, []).append(o.name)
	for row in rows:
		row.orders = by_message.get(row.name, [])


def _notes(chat: str, rows: list, more: bool, before: str | None) -> list[dict]:
	"""The team's private notes in the same stretch of time as this page of messages."""
	from periskope_connector.api.compose import comment_row

	filters = {"chat": chat}
	if more and rows:
		filters["creation"] = [">=", rows[0].message_timestamp]
	if before:
		filters["creation"] = (
			["between", [rows[0].message_timestamp, before]] if more and rows else ["<", before]
		)
	return [
		comment_row(row)
		for row in frappe.get_all(
			"Periskope Chat Comment",
			filters=filters,
			fields=["name", "content", "owner", "creation", "mentions"],
			order_by="creation asc",
			limit=500,
		)
	]


CALL_FIELDS = [
	"name",
	"direction",
	"result",
	"call_time",
	"duration",
	"handled_via",
	"answered_by",
	"summary",
	"transcript_status",
	"transcript",
	"has_recording",
	"recording_duration",
]


def _calls(chat: str, rows: list, more: bool, before: str | None) -> list[dict]:
	"""The WhatsApp calls (calls.py) in the same stretch of time as this page of messages."""
	filters = {"periskope_chat": chat}
	if more and rows:
		filters["call_time"] = [">=", rows[0].message_timestamp]
	if before:
		filters["call_time"] = (
			["between", [rows[0].message_timestamp, before]] if more and rows else ["<", before]
		)
	calls = frappe.get_all(
		"Periskope Call", filters=filters, fields=CALL_FIELDS, order_by="call_time asc", limit=500
	)
	names = {u: frappe.utils.get_fullname(u) for u in {c.answered_by for c in calls if c.answered_by}}
	for c in calls:
		c.answered_by_name = names.get(c.answered_by) or c.answered_by or ""
	return calls


def _participant_names(phones: list[str]) -> dict:
	"""Names for the people writing in a group, from the contact queue."""
	if not phones:
		return {}
	rows = frappe.get_all(
		"Periskope Contact",
		filters={"phone": ["in", list(set(phones))]},
		fields=["phone", "display_name"],
	)
	return {r.phone: r.display_name for r in rows if r.display_name}


def _quoted(chat: str, ids: list[str]) -> dict:
	"""The text of the messages that others reply to (Periskope gives the short WhatsApp id, the end
	of our stored message id)."""
	shorts = {s for s in ids if s}
	if not shorts:
		return {}
	# one query for the whole page (each id is the ending of a stored message id)
	msg = frappe.qb.DocType("Periskope Message")
	rows = (
		frappe.qb.from_(msg)
		.select(msg.name, msg.body, msg.direction, msg.message_type, msg.message_id)
		.where((msg.periskope_chat == chat) & Criterion.any([msg.message_id.like(f"%{s}") for s in shorts]))
	).run(as_dict=True)
	out = {}
	for row in rows:
		message_id = row.pop("message_id") or ""
		for short in shorts:
			if message_id.endswith(short):
				out.setdefault(short, row)
	return out


@frappe.whitelist(methods=["POST"])
def mark_seen(chat: str) -> None:
	access.require_inbox_user()
	_chat_or_403(chat)
	now = frappe.utils.now_datetime()
	name = frappe.db.get_value("Periskope Chat Seen", {"seen_key": f"{chat}|{frappe.session.user}"})
	if name:
		frappe.db.set_value("Periskope Chat Seen", name, "seen_at", now, update_modified=False)
	else:
		doc = frappe.get_doc(
			{"doctype": "Periskope Chat Seen", "chat": chat, "user": frappe.session.user, "seen_at": now}
		)
		doc.insert(ignore_permissions=True)  # the user's own read mark, after the chat check above
	from periskope_connector import read_state

	read_state.opened(chat)  # the chat's own reader makes it read for everyone (and the customer)


SAFE_NAME = re.compile(r"[^\w.\- ]+")
INLINE_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp", "application/pdf")
INLINE_PREFIXES = ("video/", "audio/")


@frappe.whitelist()
def media(message: str, thumb: int = 0):
	"""The file of a message, for the user's browser: the saved private File, else downloaded from
	Periskope on the server (its link and key stay on the server). With `thumb` an image comes
	reduced to at most THUMB_PIXELS on its long side (for the chat's bubbles); any other file ignores
	it."""
	access.require_inbox_user()
	doc = frappe.db.get_value(
		"Periskope Message",
		message,
		[
			"name",
			"periskope_chat",
			"attachment_file",
			"media_path",
			"media_filename",
			"media_mimetype",
			"message_type",
			"message_id",
			"whatsapp_number",
		],
		as_dict=True,
	)
	if not doc or not doc.periskope_chat:
		raise frappe.DoesNotExistError
	_chat_or_403(doc.periskope_chat)
	if doc.attachment_file:
		content = attachments._read(doc.attachment_file)
	elif attachments.has_file(frappe._dict(doc)):
		content = _download_media(doc)
	else:
		raise frappe.DoesNotExistError
	name = SAFE_NAME.sub("", doc.media_filename or "") or doc.name
	# WhatsApp names images by id, with no extension, so the type comes from the message.
	content_type = attachments._mime(doc) or mimetypes.guess_type(name)[0] or "application/octet-stream"
	if cint(thumb) and content_type in THUMB_TYPES:
		content, content_type = _thumbnail(content, content_type)
	frappe.local.response.filename = name
	frappe.local.response.filecontent = content
	frappe.local.response.type = "download"
	frappe.local.response.content_type = content_type
	# Only media the browser shows safely opens in place; anything else (HTML, SVG) is downloaded.
	frappe.local.response.display_content_as = (
		"inline" if content_type in INLINE_TYPES or content_type.startswith(INLINE_PREFIXES) else "attachment"
	)


THUMB_PIXELS = 480
THUMB_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")


def _thumbnail(content: bytes, content_type: str) -> tuple[bytes, str]:
	"""(bytes, type) of the image reduced to THUMB_PIXELS on its long side: a JPEG, or a PNG when it
	has transparency. An image that is small already, or that cannot be read, is returned as it is."""
	import io

	from PIL import Image, ImageOps, UnidentifiedImageError

	try:
		with Image.open(io.BytesIO(content)) as image:
			if max(image.size) <= THUMB_PIXELS:
				return content, content_type
			image = ImageOps.exif_transpose(image)  # a phone photo keeps its upright position
			image.thumbnail((THUMB_PIXELS, THUMB_PIXELS))
			transparent = image.mode in ("RGBA", "LA", "P") and (
				image.mode != "P" or "transparency" in image.info
			)
			out = io.BytesIO()
			if transparent:
				image.convert("RGBA").save(out, "PNG", optimize=True)
				return out.getvalue(), "image/png"
			image.convert("RGB").save(out, "JPEG", quality=80, optimize=True)
			return out.getvalue(), "image/jpeg"
	except (UnidentifiedImageError, OSError, ValueError):
		return content, content_type


def _download_media(doc) -> bytes:
	"""The file from its saved link; when that link has expired, from a new one (Get Message), which
	is then kept."""
	try:
		return attachments.download(doc.media_path)
	except Exception:
		from periskope_connector.api.message_actions import fresh_media_path

		path = doc.message_id and fresh_media_path(doc.message_id, doc.whatsapp_number)
		if not path or path == doc.media_path:
			raise
		content = attachments.download(path)
		frappe.db.set_value("Periskope Message", doc.name, "media_path", path, update_modified=False)
		return content


@frappe.whitelist()
def get_boot() -> dict:
	"""What the inbox needs on load: who the user is, what they may do, and their numbers."""
	access.require_inbox_user()
	return boot()


def boot() -> dict:
	user = frappe.session.user
	allowed = access.allowed_numbers(user)
	rows = numbers.all_numbers()
	if allowed is not None:
		rows = [r for r in rows if r.name in allowed]
	return {
		"user": user,
		"full_name": frappe.utils.get_fullname(user),
		"user_image": frappe.db.get_value("User", user, "user_image"),
		"is_manager": access.is_manager(user),
		"is_admin": bool(set(frappe.get_roles(user)) & {"System Manager", "WhatsApp Manager"})
		or user == "Administrator",
		"numbers": [
			{
				"name": r.name,
				"label": r.display_name or r.number,
				"is_active": r.is_active,
				"is_ready": r.is_ready,
				"wa_state": r.wa_state,
				"source": r.source or "Periskope",
			}
			for r in rows
		],
		"tags": frappe.get_all("Periskope Tag", pluck="name", order_by="name asc"),
		"brand": branding.brand(),
		# a "Test mode" badge beside the app's name while nothing reaches real customers (safety.py)
		"test_mode": safety.test_mode(),
		# "Create sales order" on a customer's message (api/sales_order.py)
		"can_create_order": bool(
			frappe.db.exists("DocType", "Sales Order") and frappe.has_permission("Sales Order", "create")
		),
		"festival": festival.current(),
	}
