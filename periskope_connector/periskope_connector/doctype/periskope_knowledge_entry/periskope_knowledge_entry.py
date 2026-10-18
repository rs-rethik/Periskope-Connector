import frappe
from frappe import _
from frappe.model.document import Document

# Which node may hang under which. "" is the Customer asks start of the tree.
PARENTS = {
	"Topic": {"", "Topic"},
	"Condition": {"Topic", "Condition"},
	"Answer": {"", "Topic", "Condition"},
}


class PeriskopeKnowledgeEntry(Document):
	def validate(self):
		self.node_type = self.node_type or "Answer"
		if self.node_type == "Answer" and not (self.answer or "").strip():
			frappe.throw(_("An answer node needs the answer text."))
		parent_type = ""
		if self.parent_entry:
			if self.parent_entry == self.name:
				frappe.throw(_("A node cannot hang under itself."))
			parent_type = frappe.db.get_value("Periskope Knowledge Entry", self.parent_entry, "node_type")
			if parent_type is None:
				frappe.throw(_("Parent {0} does not exist.").format(self.parent_entry))
		if parent_type not in PARENTS[self.node_type]:
			frappe.throw(
				_("A {0} cannot hang under {1}.").format(
					_(self.node_type), _(parent_type) if parent_type else _("Customer asks")
				)
			)
		if not self.is_new() and self.parent_entry:
			seen, node = {self.name}, self.parent_entry
			while node:
				if node in seen:
					frappe.throw(_("That connection would make a loop."))
				seen.add(node)
				node = frappe.db.get_value("Periskope Knowledge Entry", node, "parent_entry")
		if not self.is_new() and self.has_value_changed("node_type"):
			for child_type in frappe.get_all(
				"Periskope Knowledge Entry",
				filters={"parent_entry": self.name, "status": ["!=", "Retired"]},
				pluck="node_type",
			):
				if self.node_type not in PARENTS[child_type]:
					frappe.throw(_("Its connected nodes cannot hang under a {0}.").format(_(self.node_type)))
