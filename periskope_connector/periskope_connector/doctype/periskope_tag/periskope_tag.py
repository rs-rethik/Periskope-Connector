import re

import frappe
from frappe import _
from frappe.model.document import Document


class PeriskopeTag(Document):
	def validate(self):
		self.tag_name = (self.tag_name or "").strip()
		if not re.fullmatch(r"[a-z][a-z0-9_]*", self.tag_name):
			frappe.throw(
				_("Tag Name must be lowercase letters, numbers and underscores, starting with a letter")
			)
		if self.is_default and not self.active:
			frappe.throw(_("The default tag must be active"))
		if not self.is_default and not self.is_new() and self.get_doc_before_save():
			if self.get_doc_before_save().is_default:
				frappe.throw(_("Choose another default tag instead of unticking this one"))
		if not self.active and self._is_last_active():
			frappe.throw(_("At least one tag must stay active"))

	def on_update(self):
		if self.is_default:
			# One default only: the one just saved wins.
			tag = frappe.qb.DocType("Periskope Tag")
			frappe.qb.update(tag).set(tag.is_default, 0).where(
				(tag.name != self.name) & (tag.is_default == 1)
			).run()

	def on_trash(self):
		if self.is_default:
			frappe.throw(_("The default tag cannot be deleted. Make another tag the default first."))

	def _is_last_active(self) -> bool:
		others = frappe.db.count("Periskope Tag", {"active": 1, "name": ["!=", self.name]})
		return others == 0
