"""The tags the AI can give a message. They are records (Periskope Tag), so the team edits them in
Desk instead of in code. The six below are the starting set, created on install."""

import frappe

DEFAULT_TAGS = [
	(
		"quality_complaint",
		"a complaint about quality: shade, GSM, shrinkage, pilling, wash fastness, damaged, defective or wrong goods, rejection of the goods, or a problem with service after the sale.",
	),
	(
		"delivery_delay",
		"delivery is late, the customer asks where the order is, or dispatch is delayed.",
	),
	(
		"payment_issue",
		"an invoice or payment dispute, overdue or missing payment, a refund, or a payment outside the company (for example to a personal account).",
	),
	("sample_request", "the customer asks for a sample, swatch or demo."),
	(
		"order_or_enquiry",
		"a new enquiry, a price or quotation request, a product question, or placing, confirming or repeating an order.",
	),
	("general", "anything else, such as greetings, scheduling, follow-ups and chatter."),
]
FALLBACK_DEFAULT = "general"

# Which workspace card counts a starting tag: the Enquiries card or the Issues card.
DASHBOARD_GROUPS = {
	"order_or_enquiry": "Enquiry",
	"quality_complaint": "Issue",
	"payment_issue": "Issue",
}


def default_actions(tag_name: str) -> list[dict]:
	"""The starting actions of a tag: the behaviour the app had before actions were configurable.
	Every tag emails the managers about High severity messages; every tag but the default one
	shows on the CRM timeline; an enquiry from an unknown 1-to-1 number can become a lead."""
	rows = [{"action": "Email Alert", "min_severity": "High", "chat_type": "Any"}]
	if tag_name != FALLBACK_DEFAULT:
		rows.append({"action": "Timeline Comment", "min_severity": "Low", "chat_type": "Any"})
	if tag_name == "order_or_enquiry":
		rows.append({"action": "Create Lead", "min_severity": "Low", "chat_type": "1-to-1"})
	return rows


def active_tags() -> list[dict]:
	"""The active tags in the order the team set, each with its name, description and default flag."""
	rows = frappe.get_all(
		"Periskope Tag",
		filters={"active": 1},
		fields=["name", "description", "is_default"],
		order_by="sort_order asc, creation asc",
	)
	return rows


def names() -> list[str]:
	return [tag["name"] for tag in active_tags()]


def default_tag() -> str:
	"""The tag for a message the AI could not place, and for messages that are skipped."""
	tags = active_tags()
	for tag in tags:
		if tag.get("is_default"):
			return tag["name"]
	return tags[0]["name"] if tags else FALLBACK_DEFAULT


def prompt_block() -> str:
	"""The tag list as it appears in the AI prompt."""
	lines = [
		f"- {tag['name']}: {tag['description'] or tag['name'].replace('_', ' ')}" for tag in active_tags()
	]
	return (
		"Categories (category must be exactly one of these names; if none fits, use "
		f'"{default_tag()}"):\n' + "\n".join(lines)
	)
