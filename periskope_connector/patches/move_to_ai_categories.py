import frappe

from periskope_connector.ai.classifier import DEFAULT_INSTRUCTIONS

# Old tag -> new category. Anything not listed becomes "general".
QUALITY = ["Complaint", "Product Quality Issue", "Service / Support Issue"]
DELIVERY = ["Delivery Delay"]
PAYMENT = [
	"Payment Dispute",
	"Refund / Return Request",
	"Rep Off-platform Deal / Personal Payment Request",
	"Payment Reminder",
]
SAMPLE = ["Sample Request"]
ENQUIRY = [
	"New Enquiry",
	"Product Enquiry",
	"Pricing Request",
	"Quotation Request",
	"Bulk / Large Order Enquiry",
	"Purchase Intent",
	"Reorder / Repeat Order",
	"Upsell / Cross-sell Opportunity",
	"Requirement Shared",
	"Requirement Clarified",
	"Quotation Shared",
	"Discount / Price Negotiation",
	"Terms & Conditions Discussion",
	"PO / Order Confirmation Received",
	"Deal Won",
]

TAG_TO_CATEGORY = {
	**dict.fromkeys(QUALITY, "quality_complaint"),
	**dict.fromkeys(DELIVERY, "delivery_delay"),
	**dict.fromkeys(PAYMENT, "payment_issue"),
	**dict.fromkeys(SAMPLE, "sample_request"),
	**dict.fromkeys(ENQUIRY, "order_or_enquiry"),
}

OLD_DOCTYPES = ["Periskope Message Tag", "Periskope Message Tag Link"]
OLD_CARDS = ["Suggested New Leads", "Suggested Escalations", "High and Critical Issues"]
OLD_CHARTS = ["Messages by Main Tag"]


# Frappe runs execute() by name from patches.txt on `bench migrate`; do not remove it.
def execute():
	"""Move messages from the 60 tags to the six categories, fill the new fields, and remove the tags.

	The old columns are still in the table when this runs (a migrate never drops them), so the
	tags can be read once more here."""
	frappe.reload_doc("periskope_connector", "doctype", "periskope_message")
	frappe.reload_doc("periskope_connector", "doctype", "periskope_phone")
	if not frappe.db.has_column("Periskope Message", "primary_tag"):
		return  # a new site: nothing to move

	for tag, category in TAG_TO_CATEGORY.items():
		frappe.db.sql(
			"update `tabPeriskope Message` set ai_category = %s where primary_tag = %s", (category, tag)
		)
	frappe.db.sql(
		"""update `tabPeriskope Message` set ai_category = 'general'
		where classification_status = 'Done' and ifnull(primary_tag, '') != '' and ifnull(ai_category, '') = ''"""
	)

	# Severity is now Low / Medium / High.
	frappe.db.sql("update `tabPeriskope Message` set severity = 'Low' where severity = 'None'")
	frappe.db.sql("update `tabPeriskope Message` set severity = 'High' where severity = 'Critical'")
	frappe.db.sql(
		"""update `tabPeriskope Message` set severity = 'Medium'
		where ai_category in ('quality_complaint', 'payment_issue') and severity = 'Low'"""
	)

	# Message type is now text / image / document / audio / other. The raw value is kept.
	frappe.db.sql(
		"update `tabPeriskope Message` set periskope_type = message_type where ifnull(periskope_type, '') = ''"
	)
	frappe.db.sql("update `tabPeriskope Message` set message_type = 'text' where message_type = 'chat'")
	frappe.db.sql("update `tabPeriskope Message` set message_type = 'audio' where message_type = 'ptt'")
	frappe.db.sql(
		"""update `tabPeriskope Message` set message_type = 'other'
		where message_type not in ('text', 'image', 'document', 'audio')"""
	)

	# customer_phone: the last 10 digits of the customer, or of the sender in a group.
	frappe.db.sql(
		"""update `tabPeriskope Message`
		set customer_phone = right(if(is_group = 1, sender_phone, contact_number), 10)
		where ifnull(customer_phone, '') = '' and length(if(is_group = 1, sender_phone, contact_number)) >= 10"""
	)

	# replied: a customer message with a later rep message in the same chat.
	frappe.db.sql(
		"""update `tabPeriskope Message` m set m.replied = 1
		where m.direction = 'Incoming' and exists (
			select 1 from (select chat_id, max(message_timestamp) t from `tabPeriskope Message`
				where direction = 'Outgoing' group by chat_id) o
			where o.chat_id = m.chat_id and o.t >= m.message_timestamp)"""
	)

	# The saved instructions were written for the tag list.
	frappe.db.set_single_value("Periskope Settings", "ai_instructions", DEFAULT_INSTRUCTIONS)

	for doctype in OLD_DOCTYPES:
		if frappe.db.exists("DocType", doctype):
			frappe.delete_doc("DocType", doctype, force=True, ignore_missing=True)
	for name in OLD_CARDS:
		frappe.delete_doc("Number Card", name, force=True, ignore_missing=True)
	for name in OLD_CHARTS:
		frappe.delete_doc("Dashboard Chart", name, force=True, ignore_missing=True)
