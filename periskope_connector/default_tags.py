"""Default Periskope Message Tags, mapped to FCRM statuses, lost reasons, tasks and escalations.

Existing tags are never touched, so anything management edits later is kept. FCRM links are
only set when that status / reason / source exists on the site.
"""

import frappe

BI = "Buying Intent"
DP = "Deal Progress"
LQ = "Lead Qualification"
LR = "Lost Reason"
FT = "Follow-up & Task"
IE = "Issue & Escalation"
IN = "Business Insight"

# (category, tag, main action, extra fields, when to use it)
TAGS = [
	# Buying intent
	(BI, "New Enquiry", "Create Lead", {"lead_source": "WhatsApp"}, "A new person asks about our products or services for the first time. Example: 'Hi, I saw your ad. Do you supply LED panels?'"),
	(BI, "Product Enquiry", "Create Lead", {}, "Asks about a product's features, specifications, models, availability or stock."),
	(BI, "Pricing Request", "Create Lead", {}, "Asks for a price, rate, cost or rate card. Example: 'What is the price for 100 pieces?'"),
	(BI, "Quotation Request", "Create Lead", {"task_title": "Prepare quotation", "task_priority": "High"}, "Explicitly asks for a quotation, proposal or formal price offer."),
	(BI, "Sample Request", "Create Task", {"task_title": "Arrange sample", "task_priority": "Medium"}, "Asks for a sample or a demo unit to be sent."),
	(BI, "Demo / Trial Request", "Create Task", {"target_deal_status": "Demo/Making", "task_title": "Schedule demo", "task_priority": "Medium"}, "Asks for a product demo, trial or presentation."),
	(BI, "Meeting / Site Visit Request", "Create Task", {"task_title": "Schedule meeting or site visit", "task_priority": "Medium"}, "Asks to meet, or asks the rep to visit their office or site."),
	(BI, "Bulk / Large Order Enquiry", "Create Lead", {"task_title": "Call bulk enquiry customer", "task_priority": "High"}, "Asks about a large quantity, a bulk price, a dealership or a big project."),
	(BI, "Purchase Intent", "Update Deal Status", {"target_deal_status": "Ready to Close"}, "Says clearly that they want to buy, go ahead, or place the order. Example: 'Ok, we will take it. Please proceed.'"),
	(BI, "Reorder / Repeat Order", "Create Task", {"task_title": "Process repeat order", "task_priority": "Medium"}, "An existing customer wants to order the same or similar items again."),
	(BI, "Upsell / Cross-sell Opportunity", "None", {}, "The customer's message shows a chance to sell more or a related product."),
	# Deal progress
	(DP, "Requirement Shared", "Update Deal Status", {"target_deal_status": "Qualification"}, "The customer shares their detailed requirement, quantity, specification or budget."),
	(DP, "Demo Scheduled", "Update Deal Status", {"target_deal_status": "Demo/Making"}, "A demo, trial or presentation date is agreed."),
	(DP, "Quotation Shared", "Update Deal Status", {"target_deal_status": "Proposal/Quotation"}, "The rep sends a quotation, proposal or price list to the customer."),
	(DP, "Discount / Price Negotiation", "Update Deal Status", {"target_deal_status": "Negotiation"}, "Either side asks for or offers a discount, a better rate, or a price change."),
	(DP, "Terms & Conditions Discussion", "Update Deal Status", {"target_deal_status": "Negotiation"}, "Discussion about payment terms, delivery terms, warranty or contract conditions."),
	(DP, "PO / Order Confirmation Received", "Update Deal Status", {"target_deal_status": "Ready to Close", "task_title": "Process purchase order", "task_priority": "High"}, "The customer sends or confirms a purchase order or a firm order."),
	(DP, "Advance / Payment Received", "Update Deal Status", {"target_deal_status": "Ready to Close"}, "The customer says they have paid, or sends a payment proof or advance."),
	(DP, "Deal Won", "Update Deal Status", {"target_deal_status": "Won"}, "The deal is finally confirmed and closed. Example: order accepted and payment done."),
	# Lead qualification
	(LQ, "Interested", "Update Lead Status", {"target_lead_status": "Contacted"}, "The prospect replies with interest and engages in the conversation."),
	(LQ, "Requirement Clarified", "Update Lead Status", {"target_lead_status": "Qualified"}, "The prospect's need, budget or timeline is clear, so the lead is a real opportunity."),
	(LQ, "Call Later / Nurture", "Update Lead Status", {"target_lead_status": "Nurture", "task_title": "Call back later", "task_priority": "Medium"}, "Not ready now but open later. Example: 'Please contact me next month.'"),
	(LQ, "Not Interested", "Update Lead Status", {"target_lead_status": "Unqualified"}, "Clearly says they are not interested and does not want to continue."),
	(LQ, "Wrong Number / Spam", "Update Lead Status", {"target_lead_status": "Junk"}, "Wrong number, a bot, an advertisement or an unrelated message."),
	(LQ, "Unresponsive Customer", "None", {}, "The customer has stopped replying to the rep's follow-ups."),
	# Lost reasons
	(LR, "Lost to Competitor", "Mark Lost", {"lost_reason": "Competition"}, "The customer chose or is choosing a competitor. Example: 'We are going with another supplier.'"),
	(LR, "Price Too High", "Mark Lost", {"lost_reason": "Pricing"}, "Rejects because our price is too high compared to what they expected."),
	(LR, "Budget Constraint", "Mark Lost", {"lost_reason": "Budget Constraints"}, "Cannot go ahead because they do not have the budget."),
	(LR, "Missing Feature / Spec", "Mark Lost", {"lost_reason": "Missing Features"}, "Rejects because the product lacks a feature or specification they need."),
	(LR, "Decision Delayed", "Mark Lost", {"lost_reason": "Long Sales Cycle"}, "The decision is postponed for a long time or cancelled for now."),
	(LR, "Needs Decision-maker Approval", "Mark Lost", {"lost_reason": "No Decision-Maker"}, "The person cannot decide and no decision-maker is involved."),
	(LR, "Not a Fit", "Mark Lost", {"lost_reason": "Poor Fit"}, "Our product or service does not suit what they do or need."),
	# Follow-up and tasks
	(FT, "Callback Requested", "Create Task", {"task_title": "Call the customer back", "task_priority": "High"}, "The customer asks for a call. Example: 'Call me tomorrow at 4pm.'"),
	(FT, "Follow-up Promised by Rep", "Create Task", {"task_title": "Do the follow-up promised on WhatsApp", "task_priority": "Medium"}, "The rep promises to do something later. Example: 'I will send the details by evening.'"),
	(FT, "Send Documents / Brochure / Catalogue", "Create Task", {"task_title": "Send the requested documents", "task_priority": "Medium"}, "The customer asks for a brochure, catalogue, datasheet, certificate or other document."),
	(FT, "Meeting Scheduled", "Create Task", {"task_title": "Attend the scheduled meeting", "task_priority": "Medium"}, "A meeting, call or visit is fixed for a specific time."),
	(FT, "Awaiting Customer Reply", "None", {}, "The rep asked a question or sent details and is waiting for the customer."),
	(FT, "Awaiting Rep Reply", "Create Task", {"task_title": "Reply to the customer", "task_priority": "High"}, "The customer asked something and is waiting for the rep to answer."),
	# Issues and escalations
	(IE, "Complaint", "Escalate", {"escalation_severity": "High"}, "The customer is unhappy or complains about our product, service or behaviour."),
	(IE, "Delivery Delay", "Escalate", {"escalation_severity": "Medium"}, "Complains that delivery is late or asks where the order is."),
	(IE, "Product Quality Issue", "Escalate", {"escalation_severity": "High"}, "Reports damaged, defective or wrong products, or poor quality."),
	(IE, "Service / Support Issue", "Escalate", {"escalation_severity": "Medium"}, "Needs help or support after the sale, or reports a problem in using the product."),
	(IE, "Payment Dispute", "Escalate", {"escalation_severity": "High"}, "Disagrees with an invoice or amount, disputes a charge, or refuses to pay."),
	(IE, "Refund / Return Request", "Escalate", {"escalation_severity": "High"}, "Asks for a refund, a return or a replacement."),
	(IE, "Cancellation Threat / Churn Risk", "Escalate", {"escalation_severity": "High"}, "Threatens to cancel the order, stop buying, or move to a competitor. Example: 'I will cancel if this is not fixed.'"),
	(IE, "Legal Threat", "Escalate", {"escalation_severity": "Critical"}, "Mentions a lawyer, legal notice, court, police, consumer complaint or fraud."),
	(IE, "Abusive Language", "Escalate", {"escalation_severity": "High"}, "Abuse, harassment or threats, from the customer or from our rep."),
	(IE, "Rep Off-platform Deal / Personal Payment Request", "Escalate", {"escalation_severity": "Critical"}, "The rep offers to deal outside the company, or asks for payment to a personal account or number."),
	(IE, "Unauthorised Discount / Commitment by Rep", "Escalate", {"escalation_severity": "High"}, "The rep promises a discount, delivery date, warranty or term that looks beyond their authority."),
	(IE, "Confidential Data Shared", "Escalate", {"escalation_severity": "High"}, "Sensitive information such as bank details, passwords, OTPs or internal price lists is shared."),
	# Business insights
	(IN, "Competitor Mention", "None", {}, "A competitor's name, product or price is mentioned."),
	(IN, "Feature Request", "None", {}, "The customer asks for a feature or product change we do not have."),
	(IN, "Positive Feedback / Testimonial", "None", {}, "The customer praises our product, service or rep."),
	(IN, "Referral Given", "None", {}, "The customer refers another person or company to us."),
	(IN, "Payment Reminder", "None", {}, "A reminder about a payment that is due, or a promise about when they will pay."),
	(IN, "Invoice / Document Request", "None", {}, "Asks for an invoice, receipt, GST document or statement."),
	(IN, "Logistics / Delivery Update", "None", {}, "Shares dispatch, tracking or delivery information."),
	(IN, "Pricing Feedback", "None", {}, "Comments on our prices compared with the market, without rejecting the deal."),
	(IN, "Market / Seasonal Insight", "None", {}, "Shares useful information about demand, seasons, the market or their plans."),
	(IN, "General Chat", "None", {}, "Ordinary conversation with no sales meaning."),
	(IN, "Greeting", "None", {}, "Only a greeting, festival wish or thanks."),
	(IN, "Internal / Personal", "None", {}, "A personal or non-business message that management does not need to act on."),
]  # fmt: skip

LINK_FIELDS = {
	"lead_source": "CRM Lead Source",
	"target_lead_status": "CRM Lead Status",
	"target_deal_status": "CRM Deal Status",
	"lost_reason": "CRM Lost Reason",
}


def _ensure_whatsapp_source():
	if frappe.db.exists("DocType", "CRM Lead Source") and not frappe.db.exists("CRM Lead Source", "WhatsApp"):
		frappe.get_doc({"doctype": "CRM Lead Source", "source_name": "WhatsApp"}).insert(
			ignore_permissions=True
		)


def create_default_tags():
	_ensure_whatsapp_source()
	created = 0
	for category, name, action, extra, description in TAGS:
		if frappe.db.exists("Periskope Message Tag", name):
			continue
		values = {
			"doctype": "Periskope Message Tag",
			"tag_name": name,
			"category": category,
			"description": description,
			"fcrm_action": action,
			"enabled": 1,
		}
		for field, value in extra.items():
			doctype = LINK_FIELDS.get(field)
			# Skip a mapping the site does not have (its statuses may have been renamed).
			if doctype and not (frappe.db.exists("DocType", doctype) and frappe.db.exists(doctype, value)):
				continue
			values[field] = value
		frappe.get_doc(values).insert(ignore_permissions=True)
		created += 1
	return created
