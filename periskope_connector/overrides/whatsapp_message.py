"""The CRM's WhatsApp tab, sent through Periskope.

frappe_whatsapp sends every new Outgoing "WhatsApp Message" to Meta in before_insert. For rows of
the Periskope account (mirror.ACCOUNT) this class sends the rep's text through Periskope instead
(outbound.send, with the test-mode guard), pauses the AI for that chat because the rep has taken it
over, and keeps every Meta call (send, template, read receipt) away. Rows of any other account
behave exactly as in frappe_whatsapp.
"""

import re

import frappe
from frappe import _
from frappe_whatsapp.frappe_whatsapp.doctype.whatsapp_message.whatsapp_message import WhatsAppMessage

from periskope_connector import chat, mirror, outbound, safety


class PeriskopeWhatsAppMessage(WhatsAppMessage):
	def _is_periskope(self) -> bool:
		return self.whatsapp_account == mirror.ACCOUNT

	def set_whatsapp_account(self):
		# The CRM leaves the account empty and frappe_whatsapp would use the site's default, which can
		# be a Meta number the customer never wrote to. A reply goes back the way they last wrote.
		if not self.whatsapp_account and self.type == "Outgoing" and self._customer_wrote_to_periskope():
			self.whatsapp_account = mirror.ACCOUNT
		super().set_whatsapp_account()

	def _customer_wrote_to_periskope(self) -> bool:
		if not (self.reference_doctype and self.reference_name):
			return False
		last = frappe.get_all(
			"WhatsApp Message",
			filters={
				"reference_doctype": self.reference_doctype,
				"reference_name": self.reference_name,
				"type": "Incoming",
			},
			pluck="whatsapp_account",
			order_by="creation desc",
			limit=1,
		)
		return bool(last) and last[0] == mirror.ACCOUNT

	def before_insert(self):
		self.set_whatsapp_account()
		if not self._is_periskope():
			return super().before_insert()
		if self.flags.periskope_mirror or self.type != "Outgoing":
			return
		self.send_through_periskope()

	def send_through_periskope(self):
		if self.template or self.use_template:
			frappe.throw(_("Templates are not sent through Periskope. Type the message instead."))
		if self.attach or (self.content_type or "text") != "text":
			frappe.throw(_("Only text messages can be sent through Periskope from here."))
		text = (self.message or "").strip()
		if not text:
			frappe.throw(_("Type a message first."))
		chat_id, phone = self._chat_and_phone()
		try:
			result = outbound.send(
				chat_id,
				text,
				phone,
				origin="CRM Tab",
				reference_doctype=self.reference_doctype,
				reference_name=self.reference_name,
				detail={"user": frappe.session.user},
			)
		except safety.SendNotAllowed:
			frappe.throw(_("Not sent: test mode allows messages only to the test numbers."))
		unique_id = result.get("unique_id") or ""
		# Periskope's own copy (the webhook) carries message_id "true_<chat>_<unique_id>"; the same id
		# here lets mirror.upsert find this row instead of adding a second one.
		self.message_id = f"true_{chat_id}_{unique_id}" if unique_id else None
		self.status = "sent"
		self.label = "Rep reply"
		chat.pause(chat_id, frappe.session.user, "Rep replied")
		from periskope_connector.autoreply import cancel

		cancel(chat_id, _("A rep replied"))

	def _chat_and_phone(self) -> tuple[str, str]:
		chats = [
			c for c in chat.for_record(self.reference_doctype, self.reference_name) if not safety.is_group(c)
		]
		chat_id = chats[0] if chats else ""
		if not chat_id:
			digits = re.sub(r"\D", "", self.to or "")
			if not digits:
				frappe.throw(_("This record has no WhatsApp number to send to."))
			chat_id = f"{digits}@c.us"
		phone = frappe.db.get_value("Periskope Chat", chat_id, "org_phone") or frappe.db.get_value(
			"Periskope Phone", {"parent": "Periskope Settings", "is_ready": 1}, "number"
		)
		if not phone:
			frappe.throw(_("No connected company number to send from."))
		return chat_id, phone

	# Nothing of the Periskope account may reach Meta.
	def send_outgoing(self):
		if not self._is_periskope():
			return super().send_outgoing()

	def send_template(self):
		if not self._is_periskope():
			return super().send_template()

	def notify(self, data):
		if not self._is_periskope():
			return super().notify(data)

	def send_read_receipt(self):
		if not self._is_periskope():
			return super().send_read_receipt()
