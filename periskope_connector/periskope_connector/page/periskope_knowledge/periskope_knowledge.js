// The Knowledge Base canvas: the knowledge base the auto reply answers from, drawn as a node graph in
// the style of n8n. "Customer asks" starts the tree; Topic nodes branch into Condition nodes and end in
// Answer nodes. Each node is a Periskope Knowledge Entry and the wire into it is its parent_entry.
// Everything saves as it is made (knowledge_canvas.py); moving a node saves only where it sits.
frappe.pages["periskope-knowledge"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Knowledge Base"),
		single_column: true,
	});
	wrapper.canvas = new KnowledgeCanvas(page);
};

frappe.pages["periskope-knowledge"].on_page_show = function (wrapper) {
	if (wrapper.canvas) wrapper.canvas.load();
};

const KC = (() => {
	const svg = (d) =>
		`<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${d}</svg>`;
	return {
		API: "periskope_connector.knowledge_canvas.",
		NODE_W: 240,
		NODE_H: 64,
		TRIGGER: 76, // the Customer asks node is a square, centred on y = 0
		ROOT_X: 200,
		COL: 340,
		ROW: 92,
		ROOT: "",
		TYPES: {
			"": {
				label: __("Customer asks"),
				color: "#ff6d5a",
				icon: svg('<path d="M13 2 3 14h9l-1 8 10-12h-9z"/>'),
			},
			Topic: {
				label: __("Topic"),
				color: "#3b82f6",
				hint: __("A subject customers ask about, like Delivery or Pricing"),
				icon: svg(
					'<path d="M4 20h16a1 1 0 0 0 1-1V8a1 1 0 0 0-1-1h-8l-2-3H4a1 1 0 0 0-1 1v14a1 1 0 0 0 1 1z"/>'
				),
			},
			Condition: {
				label: __("Condition"),
				color: "#10b981",
				hint: __("A branch that applies only when something is true, like the city"),
				icon: svg(
					'<path d="M6 3v12"/><circle cx="18" cy="6" r="3"/><circle cx="6" cy="18" r="3"/><path d="M18 9a9 9 0 0 1-9 9"/>'
				),
			},
			Answer: {
				label: __("Answer"),
				color: "#8b5cf6",
				hint: __("What the agent tells the customer"),
				icon: svg(
					'<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'
				),
			},
		},
		// Which nodes may hang under which (the same rule as the doctype's PARENTS).
		CHILDREN: {
			"": ["Topic", "Answer"],
			Topic: ["Topic", "Condition", "Answer"],
			Condition: ["Condition", "Answer"],
			Answer: [],
		},
		e: (v) => frappe.utils.escape_html(v == null ? "" : String(v)),
	};
})();

const KC_CSS = `
.kc{position:relative;height:calc(100vh - 150px);min-height:480px;border:1px solid var(--border-color);border-radius:10px;overflow:hidden;
	background-color:var(--subtle-fg,#f7f7f7);background-image:radial-gradient(var(--gray-400,#c0c6cc) 1px,transparent 1px);user-select:none;touch-action:none;cursor:grab}
.kc.panning{cursor:grabbing}
.kc-world{position:absolute;left:0;top:0;transform-origin:0 0}
.kc-wires{position:absolute;left:-10000px;top:-10000px;width:20000px;height:20000px;pointer-events:none}
.kc-wire{fill:none;stroke:var(--gray-500,#98a1a9);stroke-width:2}
.kc-wire.temp{stroke:var(--primary);stroke-dasharray:6 5}
.kc-hit{fill:none;stroke:transparent;stroke-width:16;cursor:pointer;pointer-events:stroke}
.kc-hit:hover+.kc-wire,.kc-wire.hot{stroke:var(--primary)}
.kc-node{position:absolute;display:flex;align-items:center;gap:10px;padding:0 12px 0 10px;background:var(--card-bg,#fff);
	border:1.5px solid var(--border-color);border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.08);cursor:pointer}
.kc-node:hover{border-color:var(--gray-500,#98a1a9)}
.kc-node.sel{border-color:var(--primary);box-shadow:0 0 0 3px color-mix(in srgb,var(--primary) 22%,transparent)}
.kc-node.pending{border-style:dashed}
.kc-node.draft{border-style:dashed;border-color:var(--primary)}
.kc-ico{flex:none;width:38px;height:38px;border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff}
.kc-body{min-width:0;flex:1}
.kc-title{font-weight:600;font-size:13px;color:var(--text-color);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.kc-sub{font-size:11.5px;color:var(--text-muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:2px}
.kc-badge{position:absolute;top:-10px;left:12px;font-size:10px;font-weight:600;padding:1px 7px;border-radius:9px;background:var(--bg-orange,#fdf3d8);color:var(--text-on-orange,#8a5b00)}
.kc-in,.kc-out{position:absolute;top:50%;width:12px;height:12px;margin-top:-6px;border-radius:50%;background:var(--card-bg,#fff);border:2px solid var(--gray-500,#98a1a9)}
.kc-in{left:-7px}
.kc-out{right:-7px;cursor:crosshair}
.kc-out:hover{border-color:var(--primary);background:var(--primary)}
.kc-plus{position:absolute;top:50%;right:-48px;margin-top:-12px;width:24px;height:24px;border-radius:6px;border:1.5px solid var(--gray-500,#98a1a9);
	background:var(--card-bg,#fff);color:var(--text-muted);display:flex;align-items:center;justify-content:center;font-size:16px;line-height:1;cursor:pointer}
.kc-plus::before{content:"";position:absolute;right:24px;top:50%;width:18px;border-top:2px solid var(--gray-400,#c0c6cc)}
.kc-plus:hover{border-color:var(--primary);color:var(--primary)}
.kc-node.kids .kc-plus{opacity:0}
.kc-node.kids:hover .kc-plus{opacity:1}
.kc-trigger{justify-content:center;padding:0;border-radius:38px 10px 10px 38px}
.kc-trigger .kc-ico{width:44px;height:44px;border-radius:50%}
.kc-tlabel{position:absolute;top:100%;left:50%;transform:translateX(-50%);margin-top:6px;white-space:nowrap;font-size:12px;font-weight:600;color:var(--text-color)}
.kc-empty{position:absolute;top:100%;left:120px;margin-top:24px;white-space:nowrap;font-size:12.5px;color:var(--text-muted)}
.kc-wirebtn{position:absolute;display:none;width:26px;height:26px;margin:-13px 0 0 -13px;border-radius:6px;border:1px solid var(--border-color);
	background:var(--card-bg,#fff);color:var(--red-500,#e24c4c);align-items:center;justify-content:center;cursor:pointer;z-index:3}
.kc-zoom{position:absolute;left:12px;bottom:12px;display:flex;gap:6px;z-index:4}
.kc-zoom button{width:32px;height:32px;padding:0;border-radius:8px;border:1px solid var(--border-color);background:var(--card-bg,#fff);color:var(--text-color);font-size:15px}
.kc-zoom button:hover{border-color:var(--gray-500,#98a1a9)}
.kc-help{position:absolute;left:12px;top:10px;right:12px;font-size:12px;color:var(--text-muted);pointer-events:none;z-index:2}
.kc-menu{position:absolute;z-index:6;width:270px;background:var(--card-bg,#fff);border:1px solid var(--border-color);border-radius:10px;box-shadow:0 8px 24px rgba(0,0,0,.14);padding:6px;cursor:default}
.kc-menu .t{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);padding:4px 8px 6px}
.kc-menu .i{display:flex;gap:10px;align-items:center;padding:7px 8px;border-radius:7px;cursor:pointer}
.kc-menu .i:hover{background:var(--control-bg,#f4f5f6)}
.kc-menu .i .kc-ico{width:30px;height:30px}
.kc-menu .i b{display:block;font-size:13px;color:var(--text-color)}
.kc-menu .i span{font-size:11.5px;color:var(--text-muted)}
.kc-panel{position:absolute;top:0;right:0;bottom:0;width:380px;max-width:100%;background:var(--card-bg,#fff);border-left:1px solid var(--border-color);
	z-index:5;display:flex;flex-direction:column;cursor:default;user-select:text}
.kc-panel .h{display:flex;align-items:center;gap:10px;padding:14px 16px;border-bottom:1px solid var(--border-color)}
.kc-panel .h .kc-ico{width:32px;height:32px}
.kc-panel .h b{flex:1;font-size:14px}
.kc-panel .b{flex:1;overflow-y:auto;padding:14px 16px}
.kc-panel .f{padding:12px 16px;border-top:1px solid var(--border-color);display:flex;gap:8px}
.kc-panel label{font-size:12px;font-weight:600;color:var(--text-color);margin:10px 0 4px;display:block}
.kc-panel .d{font-size:11.5px;color:var(--text-muted);margin-top:3px}
.kc-panel .st{font-size:12px;padding:7px 10px;border-radius:7px;background:var(--control-bg,#f4f5f6);margin-bottom:6px}
.kc-panel .st.warn{background:var(--bg-orange,#fdf3d8);color:var(--text-on-orange,#8a5b00)}
.kc-gap{border:1px solid var(--border-color);border-radius:8px;padding:9px 11px;margin-bottom:8px}
.kc-gap .q{font-size:13px;color:var(--text-color);white-space:pre-wrap}
.kc-gap .w{font-size:11px;color:var(--text-muted);margin:3px 0 6px}
`;

class KnowledgeCanvas {
	constructor(page) {
		this.page = page;
		this.nodes = new Map();
		this.suggestions = {};
		this.view = { x: 80, y: 300, k: 1 };
		this.selected = null;
		this.drafts = 0;
		this.page.set_primary_action(__("Add topic"), () => this.add_child(KC.ROOT, "Topic"));
		this.page.add_inner_button(__("Import document"), () => this.import_document());
		this.gaps_btn = this.page.add_inner_button(__("Knowledge gaps"), () => this.show_gaps());
		this.page.add_menu_item(__("Tidy up layout"), () => this.tidy(true));
		this.page.add_menu_item(__("Open as list"), () =>
			frappe.set_route("List", "Periskope Knowledge Entry")
		);
		this.render();
	}

	// --- Data ----------------------------------------------------------------------------------

	call(method, args) {
		return frappe.xcall(KC.API + method, args || {});
	}

	async load() {
		const data = await this.call("get_graph");
		this.nodes = new Map(data.nodes.map((n) => [n.name, n]));
		this.suggestions = data.suggestions || {};
		this.gaps_btn.text(
			data.gaps ? __("Knowledge gaps ({0})", [data.gaps]) : __("Knowledge gaps")
		);
		const first = ![...this.nodes.values()].some((n) => n.x != null);
		if (first && this.nodes.size) this.tidy(false);
		else this.place_new();
		this.draw();
		if (!this.loaded) this.fit();
		this.loaded = true;
	}

	parent_of(n) {
		return n.parent && this.nodes.has(n.parent) ? n.parent : KC.ROOT;
	}

	children() {
		const kids = {};
		for (const n of this.nodes.values()) (kids[this.parent_of(n)] ||= []).push(n.name);
		for (const k in kids)
			kids[k].sort((a, b) => (this.nodes.get(a).y || 0) - (this.nodes.get(b).y || 0));
		return kids;
	}

	type_of(name) {
		return name === KC.ROOT ? KC.ROOT : this.nodes.get(name).node_type;
	}

	save_positions(names) {
		const positions = names
			.filter((n) => !this.nodes.get(n).draft)
			.map((n) => ({
				name: n,
				x: Math.round(this.nodes.get(n).x),
				y: Math.round(this.nodes.get(n).y),
			}));
		if (positions.length) this.call("move_nodes", { positions: JSON.stringify(positions) });
	}

	// --- Layout --------------------------------------------------------------------------------

	tidy(save) {
		const kids = this.children();
		let slot = 0;
		const place = (name, depth) => {
			const n = this.nodes.get(name);
			const ch = kids[name] || [];
			let y;
			if (!ch.length) y = slot++ * KC.ROW;
			else {
				const ys = ch.map((c) => place(c, depth + 1));
				y = (ys[0] + ys[ys.length - 1]) / 2;
			}
			n.x = KC.ROOT_X + depth * KC.COL;
			n.y = y;
			return y;
		};
		const ys = (kids[KC.ROOT] || []).map((t) => place(t, 0));
		const shift = ys.length ? -(ys[0] + ys[ys.length - 1]) / 2 - KC.NODE_H / 2 : 0;
		for (const n of this.nodes.values()) n.y += shift;
		this.save_positions([...this.nodes.keys()]);
		if (save) {
			this.draw();
			this.fit();
		}
	}

	// The first free spot at (x, y) or below it, so a new node never lands on another one.
	free_spot(x, y, skip) {
		const taken = (ty) =>
			[...this.nodes.values()].some(
				(o) =>
					o.name !== skip &&
					o.x != null &&
					Math.abs(o.x - x) < KC.NODE_W + 20 &&
					Math.abs(o.y - ty) < KC.NODE_H + 12
			);
		while (taken(y)) y += KC.ROW;
		return [x, y];
	}

	// A node with no saved position goes to the right of its parent, below its siblings.
	place_new() {
		const placed = [];
		for (let pass = 0; pass < 20; pass++) {
			let left = false;
			for (const n of this.nodes.values()) {
				if (n.x != null) continue;
				const parent = this.parent_of(n);
				const p = parent === KC.ROOT ? null : this.nodes.get(parent);
				if (p && p.x == null) {
					left = true;
					continue;
				}
				const sibs = [...this.nodes.values()].filter(
					(s) => s !== n && s.x != null && this.parent_of(s) === parent
				);
				[n.x, n.y] = this.free_spot(
					p ? p.x + KC.COL : KC.ROOT_X,
					sibs.length
						? Math.max(...sibs.map((s) => s.y)) + KC.ROW
						: p
						? p.y
						: -KC.NODE_H / 2,
					n.name
				);
				placed.push(n.name);
			}
			if (!left) break;
		}
		this.save_positions(placed);
	}

	// --- Rendering -----------------------------------------------------------------------------

	render() {
		$(`<style>${KC_CSS}</style>`).appendTo(this.page.main);
		this.$root = $(`
			<div class="kc">
				<div class="kc-help">${KC.e(
					__(
						"The agent matches a customer's message to a topic or answer, then follows the condition that holds. Drag a node's right dot onto another node to connect them. Scroll to move, Ctrl + scroll to zoom."
					)
				)}</div>
				<div class="kc-world"><svg class="kc-wires"></svg><div class="kc-nodes"></div>
					<div class="kc-wirebtn" title="${KC.e(__("Remove this connection"))}">${frappe.utils.icon(
			"delete",
			"sm"
		)}</div>
				</div>
				<div class="kc-zoom">
					<button data-z="in" title="${KC.e(__("Zoom in"))}">+</button>
					<button data-z="out" title="${KC.e(__("Zoom out"))}">−</button>
					<button data-z="fit" title="${KC.e(__("Fit to view"))}">⤢</button>
					<button data-z="tidy" title="${KC.e(__("Tidy up layout"))}">⋮⋮</button>
				</div>
			</div>`).appendTo(this.page.main);
		this.$world = this.$root.find(".kc-world");
		this.$nodes = this.$root.find(".kc-nodes");
		this.$wires = this.$root.find(".kc-wires");
		this.$wirebtn = this.$root.find(".kc-wirebtn");
		this.$root.find(".kc-zoom button").on("click", (ev) => {
			const z = ev.currentTarget.dataset.z;
			if (z === "fit") this.fit();
			else if (z === "tidy") this.tidy(true);
			else this.zoom_at(z === "in" ? 1.2 : 1 / 1.2);
		});
		this.bind();
		this.apply_view();
	}

	node_html(n, kids) {
		const t = KC.TYPES[n.node_type];
		const sub =
			n.node_type === "Answer"
				? n.answer
				: n.node_type === "Condition"
				? __("Only when this is true")
				: n.keywords || __("Topic");
		const badge = n.draft
			? __("Not saved")
			: n.status === "Suggested"
			? __("Waiting for approval")
			: "";
		const out = KC.CHILDREN[n.node_type].length
			? `<div class="kc-out"></div><div class="kc-plus" title="${KC.e(
					__("Add a node after this")
			  )}">+</div>`
			: "";
		return `<div class="kc-node ${kids[n.name] ? "kids" : ""} ${n.draft ? "draft" : ""} ${
			n.status === "Suggested" ? "pending" : ""
		} ${this.selected === n.name ? "sel" : ""}" data-name="${KC.e(n.name)}" style="left:${
			n.x
		}px;top:${n.y}px;width:${KC.NODE_W}px;height:${KC.NODE_H}px">
			<div class="kc-in"></div>
			<div class="kc-ico" style="background:${t.color}">${t.icon}</div>
			<div class="kc-body"><div class="kc-title">${KC.e(n.question || __("Untitled"))}</div>
			<div class="kc-sub">${KC.e(sub)}</div></div>
			${badge ? `<div class="kc-badge">${KC.e(badge)}</div>` : ""}${out}
		</div>`;
	}

	draw() {
		const T = KC.TRIGGER;
		const kids = this.children();
		let html = `<div class="kc-node kc-trigger ${
			kids[KC.ROOT] ? "kids" : ""
		}" data-name="" style="left:0;top:${-T / 2}px;width:${T}px;height:${T}px">
			<div class="kc-ico" style="background:${KC.TYPES[""].color}">${KC.TYPES[""].icon}</div>
			<div class="kc-out"></div><div class="kc-plus" title="${KC.e(__("Add a node after this"))}">+</div>
			<div class="kc-tlabel">${KC.e(__("Customer asks"))}</div>
			${
				this.nodes.size
					? ""
					: `<div class="kc-empty">${KC.e(__("Click + to add your first topic"))}</div>`
			}
		</div>`;
		for (const n of this.nodes.values()) html += this.node_html(n, kids);
		this.$nodes.html(html);
		this.draw_wires();
	}

	out_point(name) {
		if (name === KC.ROOT) return [KC.TRIGGER, 0];
		const n = this.nodes.get(name);
		return [n.x + KC.NODE_W, n.y + KC.NODE_H / 2];
	}

	in_point(name) {
		const n = this.nodes.get(name);
		return [n.x, n.y + KC.NODE_H / 2];
	}

	curve([x1, y1], [x2, y2]) {
		const dx = Math.max(60, Math.abs(x2 - x1) / 2);
		return `M${x1} ${y1}C${x1 + dx} ${y1} ${x2 - dx} ${y2} ${x2} ${y2}`;
	}

	draw_wires() {
		let s = "";
		for (const n of this.nodes.values()) {
			const d = this.curve(this.out_point(this.parent_of(n)), this.in_point(n.name));
			s += `<path class="kc-hit" data-child="${KC.e(
				n.name
			)}" d="${d}"/><path class="kc-wire" d="${d}"/>`;
		}
		if (this.temp)
			s += `<path class="kc-wire temp" d="${this.curve(this.temp.from, this.temp.to)}"/>`;
		// The layer is 20000 px wide, shifted so that canvas (0, 0) sits at its middle: browsers do
		// not find the pointer on SVG drawn outside the SVG's own box.
		this.$wires.html(`<g transform="translate(10000 10000)">${s}</g>`);
	}

	apply_view() {
		const { x, y, k } = this.view;
		this.$world.css("transform", `translate(${x}px,${y}px) scale(${k})`);
		this.$root.css({
			"background-size": `${20 * k}px ${20 * k}px`,
			"background-position": `${x}px ${y}px`,
		});
	}

	to_world(ev) {
		const r = this.$root[0].getBoundingClientRect();
		return [
			(ev.clientX - r.left - this.view.x) / this.view.k,
			(ev.clientY - r.top - this.view.y) / this.view.k,
		];
	}

	zoom_at(factor, cx, cy) {
		const r = this.$root[0].getBoundingClientRect();
		cx = cx ?? r.width / 2;
		cy = cy ?? r.height / 2;
		const k = Math.min(2, Math.max(0.25, this.view.k * factor));
		this.view.x = cx - ((cx - this.view.x) * k) / this.view.k;
		this.view.y = cy - ((cy - this.view.y) * k) / this.view.k;
		this.view.k = k;
		this.apply_view();
	}

	fit() {
		const r = this.$root[0].getBoundingClientRect();
		const panel = this.$panel ? 380 : 0;
		let [x0, y0, x1, y1] = [0, -KC.TRIGGER / 2, KC.TRIGGER + 60, KC.TRIGGER / 2 + 30];
		for (const n of this.nodes.values()) {
			x0 = Math.min(x0, n.x);
			y0 = Math.min(y0, n.y - 14);
			x1 = Math.max(x1, n.x + KC.NODE_W + 60);
			y1 = Math.max(y1, n.y + KC.NODE_H);
		}
		const w = Math.max(200, r.width - panel - 80);
		const h = Math.max(200, r.height - 110);
		const k = Math.min(1.2, Math.max(0.25, Math.min(w / (x1 - x0), h / (y1 - y0))));
		this.view = {
			k,
			x: 40 + (w - (x1 - x0) * k) / 2 - x0 * k,
			y: 50 + (h - (y1 - y0) * k) / 2 - y0 * k,
		};
		this.apply_view();
	}

	// Pan just enough that a node is in sight, left of the side panel.
	show_node(name) {
		const n = this.nodes.get(name);
		const { x, y, k } = this.view;
		const W = this.$root.width() - (this.$panel ? this.$panel.outerWidth() : 0);
		const H = this.$root.height();
		const [left, top] = [n.x * k + x, n.y * k + y];
		const [right, bottom] = [left + (KC.NODE_W + 60) * k, top + KC.NODE_H * k];
		if (left < 40) this.view.x += 40 - left;
		else if (right > W - 20) this.view.x -= Math.min(right - (W - 20), left - 40);
		if (top < 50) this.view.y += 50 - top;
		else if (bottom > H - 60) this.view.y -= bottom - (H - 60);
		this.apply_view();
	}

	// --- Interaction ---------------------------------------------------------------------------

	bind() {
		const root = this.$root[0];
		// The canvas moves only by its view; stop the browser scrolling it to show a menu or field.
		root.addEventListener("scroll", () => {
			root.scrollTop = 0;
			root.scrollLeft = 0;
		});
		root.addEventListener(
			"wheel",
			(ev) => {
				if (ev.target.closest(".kc-panel,.kc-menu")) return;
				ev.preventDefault();
				if (ev.ctrlKey || ev.metaKey) {
					const r = root.getBoundingClientRect();
					this.zoom_at(
						Math.exp(-ev.deltaY * 0.01),
						ev.clientX - r.left,
						ev.clientY - r.top
					);
				} else {
					this.view.x -= ev.deltaX;
					this.view.y -= ev.deltaY;
					this.apply_view();
				}
			},
			{ passive: false }
		);

		root.addEventListener("pointerdown", (ev) => {
			if (ev.button !== 0 || ev.target.closest(".kc-panel,.kc-menu,.kc-zoom,.kc-wirebtn"))
				return;
			this.close_menu();
			const $t = $(ev.target);
			const $node = $t.closest(".kc-node");
			if ($t.closest(".kc-plus").length) return; // handled on click
			const name = $node.attr("data-name") ?? "";
			if ($t.closest(".kc-out").length) return this.start_wire(ev, name);
			if ($node.length && name !== "") return this.start_drag(ev, name);
			if ($node.length) return;
			this.start_pan(ev);
		});

		this.$root.on("click", ".kc-plus", (ev) => {
			const from = $(ev.currentTarget).closest(".kc-node").attr("data-name") ?? "";
			const [x, y] = this.out_point(from);
			this.open_menu(from, x + 60, y - 20);
		});

		this.$root.on("mouseenter", ".kc-hit", (ev) => {
			clearTimeout(this.wire_timer);
			const child = String(ev.currentTarget.dataset.child);
			const n = this.nodes.get(child);
			const [x1, y1] = this.out_point(this.parent_of(n));
			const [x2, y2] = this.in_point(child);
			this.$wirebtn
				.css({ display: "flex", left: (x1 + x2) / 2, top: (y1 + y2) / 2 })
				.attr("data-child", child);
		});
		this.$root.on("mouseleave", ".kc-hit", () => {
			this.wire_timer = setTimeout(() => this.$wirebtn.hide(), 250);
		});
		this.$wirebtn
			.on("mouseenter", () => clearTimeout(this.wire_timer))
			.on("mouseleave", () => this.$wirebtn.hide())
			.on("click", () => {
				this.$wirebtn.hide();
				this.disconnect(this.$wirebtn.attr("data-child"));
			});

		$(document).on("keydown.kc", (ev) => {
			if (
				!this.$root.is(":visible") ||
				$(ev.target).is("input,textarea,select,[contenteditable]")
			)
				return;
			if ((ev.key === "Delete" || ev.key === "Backspace") && this.selected)
				this.remove(this.selected);
			if (ev.key === "Escape") {
				this.close_menu();
				this.close_panel();
			}
		});
	}

	track(ev, move, up) {
		const root = this.$root[0];
		root.setPointerCapture(ev.pointerId);
		const onmove = (e) => move(e);
		const onup = (e) => {
			root.removeEventListener("pointermove", onmove);
			root.removeEventListener("pointerup", onup);
			root.removeEventListener("pointercancel", onup);
			up(e);
		};
		root.addEventListener("pointermove", onmove);
		root.addEventListener("pointerup", onup);
		root.addEventListener("pointercancel", onup);
	}

	start_pan(ev) {
		const start = { x: ev.clientX, y: ev.clientY, vx: this.view.x, vy: this.view.y };
		this.$root.addClass("panning");
		let moved = false;
		this.track(
			ev,
			(e) => {
				moved = moved || Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y) > 3;
				this.view.x = start.vx + e.clientX - start.x;
				this.view.y = start.vy + e.clientY - start.y;
				this.apply_view();
			},
			() => {
				this.$root.removeClass("panning");
				if (!moved) this.select(null);
			}
		);
	}

	start_drag(ev, name) {
		const n = this.nodes.get(name);
		const start = { x: ev.clientX, y: ev.clientY, nx: n.x, ny: n.y };
		const el = this.$nodes.find(`.kc-node[data-name="${CSS.escape(name)}"]`)[0];
		let moved = false;
		this.track(
			ev,
			(e) => {
				const dx = (e.clientX - start.x) / this.view.k;
				const dy = (e.clientY - start.y) / this.view.k;
				moved = moved || Math.abs(dx) + Math.abs(dy) > 3;
				if (!moved) return;
				n.x = Math.round(start.nx + dx);
				n.y = Math.round(start.ny + dy);
				el.style.left = `${n.x}px`;
				el.style.top = `${n.y}px`;
				this.draw_wires();
			},
			() => {
				if (moved) this.save_positions([name]);
				else this.open_panel(name);
			}
		);
	}

	start_wire(ev, from) {
		this.temp = { from: this.out_point(from), to: this.to_world(ev) };
		this.draw_wires();
		this.track(
			ev,
			(e) => {
				this.temp.to = this.to_world(e);
				this.draw_wires();
			},
			(e) => {
				const [x, y] = this.temp.to;
				this.temp = null;
				this.draw_wires();
				const target = document
					.elementFromPoint(e.clientX, e.clientY)
					?.closest(".kc-node");
				const to = target ? target.dataset.name ?? "" : null;
				if (to === null) this.open_menu(from, x, y - 20, [x, y - KC.NODE_H / 2]);
				else if (to !== "" && to !== from) this.connect(to, from);
			}
		);
	}

	// --- Changes -------------------------------------------------------------------------------

	async connect(child, parent) {
		const n = this.nodes.get(child);
		if (n.draft) return;
		if (!KC.CHILDREN[this.type_of(parent)].includes(n.node_type)) {
			return frappe.show_alert({
				message: __("A {0} cannot come after {1}.", [
					KC.TYPES[n.node_type].label,
					parent === KC.ROOT
						? __("Customer asks")
						: KC.TYPES[this.type_of(parent)].label,
				]),
				indicator: "orange",
			});
		}
		Object.assign(n, await this.call("connect", { child, parent }));
		this.draw();
	}

	disconnect(child) {
		const n = this.nodes.get(child);
		if (!KC.CHILDREN[KC.ROOT].includes(n.node_type)) {
			return frappe.show_alert({
				message: __(
					"A condition must come after a topic. Connect it to another node, or delete it."
				),
				indicator: "orange",
			});
		}
		this.connect(child, KC.ROOT);
	}

	open_menu(from, x, y, at) {
		this.close_menu();
		const items = KC.CHILDREN[this.type_of(from)]
			.map(
				(t) =>
					`<div class="i" data-t="${t}"><div class="kc-ico" style="background:${
						KC.TYPES[t].color
					}">${KC.TYPES[t].icon}</div><div><b>${KC.e(KC.TYPES[t].label)}</b><span>${KC.e(
						KC.TYPES[t].hint
					)}</span></div></div>`
			)
			.join("");
		this.$menu = $(
			`<div class="kc-menu"><div class="t">${KC.e(__("Add a node"))}</div>${items}</div>`
		).appendTo(this.$root);
		const W = this.$root.width();
		const H = this.$root.height();
		this.$menu.css({
			left: Math.max(
				8,
				Math.min(x * this.view.k + this.view.x, W - this.$menu.outerWidth() - 8)
			),
			top: Math.max(
				8,
				Math.min(y * this.view.k + this.view.y, H - this.$menu.outerHeight() - 8)
			),
		});
		this.$menu.find(".i").on("click", (ev) => {
			const type = ev.currentTarget.dataset.t;
			this.close_menu();
			this.add_child(from, type, at);
		});
	}

	close_menu() {
		if (this.$menu) this.$menu.remove();
		this.$menu = null;
	}

	add_child(parent, type, at, question) {
		this.discard_drafts();
		const name = `new-${++this.drafts}`;
		const sibs = [...this.nodes.values()].filter((s) => this.parent_of(s) === parent);
		const [px, py] = this.out_point(parent);
		const [x, y] =
			at ||
			this.free_spot(
				parent === KC.ROOT ? KC.ROOT_X : px + KC.COL - KC.NODE_W,
				sibs.length ? Math.max(...sibs.map((s) => s.y)) + KC.ROW : py - KC.NODE_H / 2
			);
		this.nodes.set(name, {
			name,
			draft: true,
			node_type: type,
			parent: parent || "",
			question: question || "",
			answer: "",
			keywords: "",
			x,
			y,
		});
		this.draw();
		this.open_panel(name);
	}

	discard_drafts() {
		for (const [name, n] of this.nodes) if (n.draft) this.nodes.delete(name);
	}

	select(name) {
		this.selected = name;
		this.$nodes.find(".kc-node").removeClass("sel");
		if (name) this.$nodes.find(`.kc-node[data-name="${CSS.escape(name)}"]`).addClass("sel");
		if (!name) this.close_panel();
	}

	remove(name) {
		const n = this.nodes.get(name);
		if (n.draft) return this.close_panel();
		const below = this.count_below(name);
		frappe.confirm(
			below
				? __("Delete {0} and the {1} node(s) after it? The agent will stop using them.", [
						`<b>${KC.e(n.question)}</b>`,
						below,
				  ])
				: __("Delete {0}? The agent will stop using it.", [`<b>${KC.e(n.question)}</b>`]),
			async () => {
				await this.call("remove_node", { name });
				this.close_panel();
				this.load();
			}
		);
	}

	count_below(name) {
		const kids = this.children();
		const walk = (n) => (kids[n] || []).reduce((sum, c) => sum + 1 + walk(c), 0);
		return walk(name);
	}

	// --- The side panel ------------------------------------------------------------------------

	open_panel(name) {
		this.close_menu();
		this.close_panel(true);
		let dropped = false;
		for (const [k, v] of this.nodes) if (v.draft && k !== name) dropped = this.nodes.delete(k);
		if (dropped) this.draw();
		const n = this.nodes.get(name);
		this.select(name);
		const t = KC.TYPES[n.node_type];
		const field = (key, label, desc, rows) =>
			`<label>${KC.e(label)}</label>${
				rows
					? `<textarea class="form-control" data-f="${key}" rows="${rows}">${KC.e(
							n[key]
					  )}</textarea>`
					: `<input class="form-control" data-f="${key}" value="${KC.e(n[key])}">`
			}${desc ? `<div class="d">${KC.e(desc)}</div>` : ""}`;
		let body = "";
		if (n.status === "Suggested" && n.node_type !== "Answer") {
			body += `<div class="st warn">${KC.e(
				__(
					"The agent proposed this topic for an answer it learned. It is approved together with that answer."
				)
			)}</div>`;
		} else if (n.status === "Suggested") {
			const sug = this.suggestions[n.name];
			body += `<div class="st warn">${KC.e(
				__(
					"The agent learned this from a rep's answer. It is used only after an approver accepts it."
				)
			)}${
				sug
					? ` <a href="/app/periskope-suggestion/${encodeURIComponent(sug)}">${KC.e(
							__("Open the suggestion")
					  )}</a>`
					: ""
			}</div>`;
		} else if (!n.draft && n.node_type === "Answer") {
			body += `<div class="st">${KC.e(
				n.last_used
					? __("Last used in a reply {0}", [frappe.datetime.comment_when(n.last_used)])
					: __("Not used in a reply yet")
			)}</div>`;
		}
		if (n.node_type === "Topic") {
			body += field("question", __("Topic"), __("For example: Delivery, Pricing, Returns"));
			body += field(
				"keywords",
				__("Words customers use"),
				__("Separated by commas, for example: shipping, courier, dispatch")
			);
		} else if (n.node_type === "Condition") {
			body += field(
				"question",
				__("Applies when"),
				__(
					"For example: Customer is in Chennai. The agent checks the chat and the CRM record."
				)
			);
			body += `<button class="btn btn-xs btn-default kc-otherwise" style="margin-top:6px">${KC.e(
				__("Use Otherwise (none of the other branches)")
			)}</button>`;
		} else {
			body += field(
				"question",
				__("Question"),
				__("What the customer asks, in plain words"),
				2
			);
			body += field(
				"answer",
				__("Answer"),
				__(
					"Only facts the agent may tell any customer. Numbers are checked against this text."
				),
				5
			);
			body += field("keywords", __("Words customers use"), __("Separated by commas"));
		}
		this.$panel = $(`<div class="kc-panel">
			<div class="h"><div class="kc-ico" style="background:${t.color}">${t.icon}</div><b>${KC.e(
			n.draft ? __("New {0}", [t.label]) : t.label
		)}</b><button class="btn btn-xs btn-default kc-close">✕</button></div>
			<div class="b">${body}</div>
			<div class="f"><button class="btn btn-primary btn-sm kc-save">${KC.e(__("Save"))}</button>
			${
				n.draft
					? ""
					: `<button class="btn btn-default btn-sm kc-del">${KC.e(__("Delete"))}</button>
			<a class="btn btn-default btn-sm" style="margin-left:auto" href="/app/periskope-knowledge-entry/${encodeURIComponent(
				n.name
			)}">${KC.e(__("Open form"))}</a>`
			}</div>
		</div>`).appendTo(this.$root);
		this.$panel.find(".kc-close").on("click", () => this.close_panel());
		this.$panel.find(".kc-del").on("click", () => this.remove(name));
		this.$panel
			.find(".kc-otherwise")
			.on("click", () => this.$panel.find('[data-f="question"]').val(__("Otherwise")));
		this.$panel.find(".kc-save").on("click", () => this.save(name));
		this.$panel.on("keydown", "input", (ev) => {
			if (ev.key === "Enter") this.save(name);
		});
		setTimeout(
			() => this.$panel?.find("input,textarea")[0]?.focus({ preventScroll: true }),
			50
		);
		this.show_node(name);
	}

	close_panel(keep_selection) {
		if (this.$panel) this.$panel.remove();
		this.$panel = null;
		const had_draft = [...this.nodes.values()].some((n) => n.draft);
		if (!keep_selection) {
			this.discard_drafts();
			this.selected = null;
			if (had_draft) this.draw();
			else this.$nodes.find(".kc-node").removeClass("sel");
		}
	}

	async save(name) {
		const n = this.nodes.get(name);
		const values = {};
		this.$panel.find("[data-f]").each((_, el) => (values[el.dataset.f] = el.value.trim()));
		if (!values.question)
			return frappe.show_alert({
				message: __("Give it a name first."),
				indicator: "orange",
			});
		if (n.node_type === "Answer" && !values.answer)
			return frappe.show_alert({
				message: __("Write the answer first."),
				indicator: "orange",
			});
		const saved = await this.call("save_node", {
			node: JSON.stringify({
				name: n.draft ? null : n.name,
				node_type: n.node_type,
				parent_entry: n.parent || null,
				question: values.question,
				answer: values.answer ?? n.answer,
				keywords: values.keywords ?? n.keywords,
				x: Math.round(n.x),
				y: Math.round(n.y),
			}),
		});
		this.nodes.delete(name);
		this.nodes.set(saved.name, { ...saved, x: n.x, y: n.y });
		this.close_panel();
		this.selected = saved.name;
		this.load(); // also refreshes the gaps count
	}

	// --- Gaps and documents --------------------------------------------------------------------

	async show_gaps() {
		this.close_panel();
		const rows = await this.call("gaps");
		const list = rows.length
			? rows
					.map(
						(g, i) => `<div class="kc-gap"><div class="q">${KC.e(g.question)}</div>
						<div class="w">${KC.e(frappe.datetime.comment_when(g.when))}</div>
						<button class="btn btn-xs btn-default" data-i="${i}">${KC.e(__("Add an answer"))}</button></div>`
					)
					.join("")
			: `<div class="text-muted small">${KC.e(
					__("Nothing yet. Every recent question had an answer.")
			  )}</div>`;
		this.$panel = $(`<div class="kc-panel">
			<div class="h"><b>${KC.e(
				__("Knowledge gaps")
			)}</b><button class="btn btn-xs btn-default kc-close">✕</button></div>
			<div class="b"><div class="st">${KC.e(
				__(
					"What customers asked in the last 14 days that the agent could not answer, so it sent the holding message."
				)
			)}</div>${list}</div></div>`).appendTo(this.$root);
		this.$panel.find(".kc-close").on("click", () => this.close_panel());
		this.$panel.find("button[data-i]").on("click", (ev) => {
			const gap = rows[ev.currentTarget.dataset.i];
			this.add_child(KC.ROOT, "Answer", null, gap.question.slice(0, 140));
		});
	}

	import_document() {
		new frappe.ui.FileUploader({
			allow_multiple: false,
			make_attachments_public: false,
			restrictions: { allowed_file_types: [".pdf", ".xlsx", ".csv", ".txt", ".docx"] },
			on_success: async (file) => {
				const r = await frappe.call({
					method: KC.API + "import_document",
					args: { file_url: file.file_url },
					freeze: true,
					freeze_message: __("Reading {0}...", [file.file_name]),
				});
				const out = r.message || {};
				if (out.error) return frappe.msgprint(out.error);
				this.preview_import(out, file.file_name);
			},
		});
	}

	preview_import(out, file_name) {
		const parents = [...this.nodes.values()].filter(
			(n) => !n.draft && n.node_type !== "Answer"
		);
		const sel = this.selected && this.nodes.get(this.selected);
		const d = new frappe.ui.Dialog({
			title: __("Answers found in {0}", [file_name]),
			size: "large",
			fields: [
				{
					fieldname: "parent",
					fieldtype: "Select",
					label: __("Put them after"),
					options: [{ value: "", label: __("Customer asks") }].concat(
						parents.map((p) => ({
							value: p.name,
							label: `${KC.TYPES[p.node_type].label}: ${p.question}`,
						}))
					),
					default: sel && sel.node_type !== "Answer" && !sel.draft ? sel.name : "",
				},
				{ fieldname: "list", fieldtype: "HTML" },
			],
			primary_action_label: __("Add the ticked answers"),
			primary_action: async (v) => {
				const picks = d.$wrapper
					.find("input[data-i]:checked")
					.map((_, el) => Number(el.dataset.i))
					.get();
				if (!picks.length) return d.hide();
				const r = await this.call("confirm_import", {
					token: out.token,
					picks: JSON.stringify(picks),
					parent: v.parent || null,
				});
				d.hide();
				frappe.show_alert({
					message: __("Added {0} answer(s)", [r.saved.length]),
					indicator: "green",
				});
				this.load();
			},
		});
		d.fields_dict.list.$wrapper.html(
			`<div class="text-muted small" style="margin-bottom:8px">${KC.e(
				__(
					"Untick anything the agent should not tell customers. Nothing is saved until you add them."
				)
			)}</div>` +
				out.entries
					.map(
						(
							e,
							i
						) => `<label class="kc-gap" style="display:flex;gap:10px;cursor:pointer;font-weight:normal">
						<input type="checkbox" data-i="${i}" checked style="margin-top:3px">
						<div><div class="q"><b>${KC.e(e.question)}</b></div><div class="q">${KC.e(
							e.answer
						)}</div></div></label>`
					)
					.join("")
		);
		d.show();
	}
}
