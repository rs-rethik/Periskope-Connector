// The Knowledge Base as a full desk page. The canvas itself is public/js/periskope_knowledge.js; the
// workspace opens the same canvas as a dialog.
frappe.pages["periskope-knowledge"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Knowledge Base"),
		single_column: true,
	});
	wrapper.canvas = new periskope.KnowledgeCanvas(periskope.knowledge_page_host(page));
};

frappe.pages["periskope-knowledge"].on_page_show = function (wrapper) {
	if (wrapper.canvas) wrapper.canvas.load();
};
