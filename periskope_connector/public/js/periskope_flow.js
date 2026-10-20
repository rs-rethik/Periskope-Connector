// The Flow dialog of Periskope Settings: the pipeline as a diagram, with a panel for each step.
// The AI decides what happens to a message, so there are few controls: auto reply, working hours, the
// email lists and the tags' on/off. Each change is saved as it is made.

(() => {
	const WIDE_AT = 1120; // px of dialog width from which the panel sits to the right
	const COL = { A: 24, B: 272, C: 520, D: 768 };
	const ROW = { 1: 60, 2: 255, 3: 450, 4: 645 };
	const NODES = [
		{
			id: "periskope",
			col: "A",
			row: 1,
			title: __("Periskope"),
			sub: __("Your WhatsApp numbers"),
		},
		{
			id: "capture",
			col: "B",
			row: 1,
			title: __("Capture"),
			sub: __("Webhook + hourly catch-up"),
		},
		{
			id: "link",
			col: "C",
			row: 1,
			title: __("Link to CRM"),
			sub: __("Lead or deal by number"),
		},
		{ id: "queue", col: "D", row: 1, title: __("Contact queue"), sub: __("Unknown numbers") },
		{
			id: "files",
			col: "A",
			row: 2,
			title: __("Attachments"),
			sub: __("Saved on the lead or deal"),
		},
		{
			id: "ai",
			col: "B",
			row: 2,
			title: __("AI reads"),
			sub: __("Text, images, PDFs, voice"),
		},
		{
			id: "tags",
			col: "C",
			row: 2,
			title: __("Tags"),
			sub: __("Categories for reports"),
		},
		{
			id: "decisions",
			col: "D",
			row: 2,
			title: __("AI decides"),
			sub: __("Tasks, escalation, leads"),
		},
		{
			id: "waiting",
			col: "B",
			row: 3,
			title: __("Customer waiting?"),
			sub: __("Working hours only"),
		},
		{
			id: "sla",
			col: "C",
			row: 3,
			title: __("Reminder ladder"),
			sub: __("bell, task, managers, MD"),
		},
		{ id: "people", col: "D", row: 3, title: __("People told"), sub: __("Email and bell") },
		{
			id: "autoreply",
			col: "A",
			row: 4,
			title: __("Auto reply"),
			sub: __("When no rep answers in time"),
		},
		{
			id: "crm",
			col: "B",
			row: 4,
			title: __("CRM updates"),
			sub: __("Summary, notes, tasks"),
		},
		{
			id: "agent",
			col: "C",
			row: 4,
			title: __("Learning agent"),
			sub: __("Learns; asks before config changes"),
		},
	];
	const EDGES = [
		["M224 105H272"],
		["M472 105H520"],
		["M720 105H768", "dash", __("no match"), 744, 97, "middle"],
		["M372 150V253", "", __("every message"), 380, 205],
		["M372 150V190H124V253", "dash", __("files"), 132, 184],
		["M224 300H272"],
		["M472 300H520"],
		["M720 300H768"],
		["M372 345V448", "", __("needs a reply"), 380, 400],
		["M472 495H520"],
		["M720 495H768"],
		["M868 345V448", "", __("escalate"), 876, 400],
		["M868 255V190H620V152", "dash", __("lead, timeline comment"), 636, 184],
		["M372 540V598H124V643", "", __("auto reply"), 250, 592],
		["M224 690H272"],
		["M472 690H520"],
	];
	const e = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

	const CSS = `
.pf{display:block;margin:8px 0}
.pf.wide{display:grid;grid-template-columns:minmax(0,1fr) 400px;gap:16px;align-items:start}
.pf.wide .pf-panel{margin-top:0;position:sticky;top:12px}
.pf-note{background:var(--bg-orange,#fdf3d8);color:var(--text-on-orange,#8a5b00);border-radius:6px;padding:7px 12px;font-size:12.5px;margin-bottom:10px}
.pf-legend{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--text-muted);font-size:12px;margin-bottom:8px;align-items:center}
.pf-legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.pf-canvas{position:relative;height:min(66vh,760px);min-height:420px;overflow:hidden;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg,#f4f5f6);cursor:grab;user-select:none;touch-action:none}
.pf-canvas.panning{cursor:grabbing}
.pf-view{position:absolute;left:0;top:0;transform-origin:0 0}
.pf-view svg{display:block;width:1000px;height:805px}
.pf-zoom{position:absolute;left:10px;bottom:10px;display:flex;gap:6px;z-index:2}
.pf-zoom button{width:30px;height:30px;padding:0;border-radius:8px;border:1px solid var(--border-color);background:var(--card-bg,#fff);color:var(--text-color);font-size:15px;cursor:pointer}
.pf-zoom button:hover{border-color:var(--gray-500,#98a1a9)}
.pf svg text{font-family:inherit}
.pf .band{fill:var(--card-bg,#fff);opacity:.55}
.pf .lane{font-size:11px;letter-spacing:.09em;text-transform:uppercase;fill:var(--text-muted);font-weight:600}
.pf .edge{fill:none;stroke:var(--text-muted);stroke-width:1.5;opacity:.75}
.pf .edge.dash{stroke-dasharray:5 4}
.pf .elab{font-size:10.5px;fill:var(--text-muted)}
.pf .node{cursor:pointer;outline:none}
.pf .node rect.card{fill:var(--card-bg,#fff);stroke:var(--border-color);stroke-width:1.2}
.pf .node:hover rect.card,.pf .node:focus-visible rect.card{stroke:var(--primary)}
.pf .node.sel rect.card{stroke:var(--primary);stroke-width:2.4}
.pf .node .t{font-size:13.5px;font-weight:650;fill:var(--text-color)}
.pf .node .s{font-size:12px;fill:var(--text-muted)}
.pf .node .m{font-size:12px;fill:var(--text-color);font-family:var(--font-stack-mono,monospace)}
.pf .node.ok .bar{fill:var(--green-500,#2f9e5c)}.pf .node.warn .bar{fill:var(--orange-500,#d98b00)}.pf .node.bad .bar{fill:var(--red-500,#d6433a)}.pf .node.off .bar{fill:var(--gray-400,#aaa)}
.pf .chip{fill:var(--bg-green,#e3f3ea)}.pf .chip-t{font-size:10.5px;font-weight:600;fill:var(--text-on-green,#1e7a4b)}
.pf-panel{margin-top:14px;border:1px solid var(--border-color);border-radius:8px;padding:14px 16px;background:var(--card-bg,#fff);min-width:0}
.pf-panel .kick{font-size:11.5px;color:var(--text-muted);text-transform:uppercase;letter-spacing:.06em}
.pf-panel h3{font-size:17px;margin:3px 0 4px}
.pf-panel p{color:var(--text-muted);margin:0 0 8px}
.pf-kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;margin:10px 0;font-size:13px}
.pf-kv dt{color:var(--text-muted);font-weight:400}.pf-kv dd{margin:0;text-align:right}
.pf-box{border:1px solid var(--border-color);border-radius:6px;padding:9px 11px;margin:9px 0;background:var(--control-bg,#f7f7f8)}
.pf-box h4{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--text-muted);margin:0 0 6px}
.pf-t{width:100%;border-collapse:collapse;font-size:12.5px}
.pf-t th{font-size:10.5px;text-transform:uppercase;letter-spacing:.05em;color:var(--text-muted);font-weight:600;text-align:left;padding:3px 4px}
.pf-t td{padding:4px;border-top:1px solid var(--border-color);vertical-align:middle}
.pf-t .c{text-align:center}
.pf-t input,.pf-t select,.pf-box textarea,.pf-box input,.pf-box select{width:100%;font-size:12.5px;padding:3px 6px;border:1px solid var(--border-color);border-radius:4px;background:var(--card-bg,#fff);color:var(--text-color)}
.pf-box textarea{min-height:46px}
.pf-dialog .modal-dialog{max-width:min(1500px,96vw)}
.pf-sw{display:inline-block;width:30px;height:16px;border-radius:9px;background:var(--gray-400,#bbb);position:relative;border:0;padding:0;vertical-align:middle;cursor:pointer}
.pf-sw::after{content:"";position:absolute;left:2px;top:2px;width:12px;height:12px;border-radius:50%;background:#fff;transition:left .12s}
.pf-sw.on{background:var(--primary)}.pf-sw.on::after{left:16px}
.pf-tick{width:18px;height:18px;border-radius:4px;border:1.5px solid var(--border-color);background:var(--card-bg,#fff);cursor:pointer;position:relative;padding:0}
.pf-tick.on{background:var(--primary);border-color:var(--primary)}
.pf-tick.on::after{content:"";position:absolute;left:5px;top:1px;width:4px;height:9px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}
.pf-row{display:flex;align-items:center;justify-content:space-between;gap:10px;padding:4px 0;font-size:13px}
.pf-btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
.pf-tl{position:relative;margin:50px 10px 46px;height:4px;background:var(--border-color);border-radius:2px}
.pf-tl i{position:absolute;top:-6px;width:16px;height:16px;border-radius:50%;background:var(--primary);border:3px solid var(--card-bg,#fff);transform:translateX(-50%)}
.pf-tl i.hi{background:var(--red-500,#d6433a)}
.pf-tl b{position:absolute;top:16px;transform:translateX(-50%);font-size:11px;font-weight:600;white-space:nowrap;text-align:center;line-height:1.25}
.pf-tl b.up{top:-38px}.pf-tl b small{display:block;color:var(--text-muted);font-weight:400}
.pf-warn{color:var(--text-on-orange,#8a5b00);font-size:12px}
`;

	function ensureCss() {
		if (document.getElementById("periskope-flow-css")) return;
		const style = document.createElement("style");
		style.id = "periskope-flow-css";
		style.textContent = CSS;
		document.head.appendChild(style);
	}

	const sw = (on, attrs) =>
		`<button type="button" class="pf-sw${on ? " on" : ""}" aria-pressed="${
			on ? "true" : "false"
		}" ${attrs}></button>`;
	const kv = (rows) =>
		`<dl class="pf-kv">${rows
			.map((r) => `<dt>${e(r[0])}</dt><dd>${e(r[1])}</dd>`)
			.join("")}</dl>`;
	const box = (title, inner) => `<div class="pf-box"><h4>${e(title)}</h4>${inner}</div>`;
	const btn = (label, attrs, primary) =>
		`<button type="button" class="btn btn-xs ${
			primary ? "btn-primary" : "btn-default"
		}" ${attrs}>${e(label)}</button>`;
	const field = (frm, name) => frm._pf.data.settings[name];
	const switchRow = (frm, label, name) =>
		`<div class="pf-row"><span>${e(label)}</span>${sw(
			!!field(frm, name),
			`data-field="${name}"`
		)}</div>`;
	const minutes = (m) => (m < 60 ? `${m} ${__("min")}` : `${+(m / 60).toFixed(1)} ${__("h")}`);

	function nodeMetric(id, d) {
		const f = d[id] || {};
		return f.headline || "";
	}

	// --- moving the diagram: drag to pan, Ctrl + scroll or the buttons to zoom, fit to view ---

	const VIEW_W = 1000;
	const VIEW_H = 805;

	function applyView(frm, moved) {
		if (moved) frm._pf.moved = true;
		const { x, y, k } = frm._pf.view;
		const view = frm._pf.$body[0].querySelector(".pf-view");
		if (view) view.style.transform = `translate(${x}px,${y}px) scale(${k})`;
	}

	function fitView(frm, canvas) {
		const w = canvas.clientWidth;
		if (!w) return;
		const h = canvas.clientHeight;
		const k = Math.min(1.4, Math.max(0.3, Math.min((w - 20) / VIEW_W, (h - 20) / VIEW_H)));
		frm._pf.view = { k, x: (w - VIEW_W * k) / 2, y: (h - VIEW_H * k) / 2 };
		applyView(frm);
	}

	function zoomAt(frm, canvas, factor, cx, cy) {
		const v = frm._pf.view;
		if (!v) return;
		cx = cx ?? canvas.clientWidth / 2;
		cy = cy ?? canvas.clientHeight / 2;
		const k = Math.min(2.5, Math.max(0.3, v.k * factor));
		frm._pf.view = { k, x: cx - ((cx - v.x) * k) / v.k, y: cy - ((cy - v.y) * k) / v.k };
		applyView(frm, true);
	}

	function bindView(frm, canvas) {
		const st = frm._pf;
		// a redraw (new figures, a wider dialog) keeps where the user moved it
		if (st.view) applyView(frm);
		else {
			// fit as soon as the canvas has a size (the dialog or the page may still be opening), and
			// again when it changes, until the user moves the diagram
			st.moved = false;
			if (st.fitter) st.fitter.disconnect();
			st.fitter = new ResizeObserver(() => {
				if (st.moved || !canvas.isConnected) return st.fitter.disconnect();
				if (canvas.clientWidth) fitView(frm, canvas);
			});
			st.fitter.observe(canvas);
		}
		canvas.querySelectorAll(".pf-zoom button").forEach((b) =>
			b.addEventListener("click", () => {
				if (b.dataset.z === "fit") fitView(frm, canvas);
				else zoomAt(frm, canvas, b.dataset.z === "in" ? 1.2 : 1 / 1.2);
			})
		);
		canvas.addEventListener(
			"wheel",
			(ev) => {
				ev.preventDefault();
				if (!st.view) return;
				if (ev.ctrlKey || ev.metaKey) {
					const r = canvas.getBoundingClientRect();
					zoomAt(
						frm,
						canvas,
						Math.exp(-ev.deltaY * 0.01),
						ev.clientX - r.left,
						ev.clientY - r.top
					);
				} else {
					st.view.x -= ev.deltaX;
					st.view.y -= ev.deltaY;
					applyView(frm, true);
				}
			},
			{ passive: false }
		);
		canvas.addEventListener("pointerdown", (ev) => {
			if (ev.button !== 0 || !st.view || ev.target.closest(".pf-zoom")) return;
			const start = { x: ev.clientX, y: ev.clientY, vx: st.view.x, vy: st.view.y };
			st.dragged = false;
			const move = (m) => {
				const dx = m.clientX - start.x;
				const dy = m.clientY - start.y;
				if (!st.dragged && Math.abs(dx) + Math.abs(dy) < 4) return; // still a click
				st.dragged = true;
				canvas.classList.add("panning");
				st.view.x = start.vx + dx;
				st.view.y = start.vy + dy;
				applyView(frm, true);
			};
			const up = () => {
				canvas.classList.remove("panning");
				window.removeEventListener("pointermove", move);
				window.removeEventListener("pointerup", up);
				// the click that ends a drag must not open a step
				setTimeout(() => (st.dragged = false), 0);
			};
			window.addEventListener("pointermove", move);
			window.addEventListener("pointerup", up);
		});
	}

	function drawSvg(data, selected) {
		let s = `<svg viewBox="0 0 1000 805" role="group" aria-label="${e(
			__("How a WhatsApp message moves through the app")
		)}">`;
		s += `<defs><marker id="pf-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--text-muted)"/></marker></defs>`;
		[20, 215, 410, 605].forEach(
			(y) => (s += `<rect class="band" x="0" y="${y}" width="1000" height="150"/>`)
		);
		[
			[38, __("1 · Capture")],
			[233, __("2 · Understand")],
			[428, __("3 · Follow up")],
			[623, __("4 · AI agent")],
		].forEach(([y, t]) => (s += `<text class="lane" x="14" y="${y}">${e(t)}</text>`));
		EDGES.forEach(([d, cls, label, x, y, anchor]) => {
			s += `<path class="edge ${cls || ""}" marker-end="url(#pf-ar)" d="${d}"/>`;
			if (label)
				s += `<text class="elab" x="${x}" y="${y}"${
					anchor ? ` text-anchor="${anchor}"` : ""
				}>${e(label)}</text>`;
		});
		NODES.forEach((n) => {
			const f = data[n.id] || {};
			s += `<g class="node ${f.status || "ok"}${
				n.id === selected ? " sel" : ""
			}" tabindex="0" role="button" data-node="${n.id}" transform="translate(${COL[n.col]},${
				ROW[n.row]
			})">`;
			s += `<rect class="card" width="200" height="90" rx="8"/><rect class="bar" width="5" height="90" rx="2"/>`;
			s += `<text class="t" x="18" y="26">${e(
				n.title
			)}</text><text class="s" x="18" y="44">${e(n.sub)}</text>`;
			s += `<text class="m" data-metric="${n.id}" x="18" y="72">${e(
				nodeMetric(n.id, data)
			)}</text>`;
			s += `</g>`;
		});
		return s + `</svg>`;
	}

	// ---- panels -------------------------------------------------------------------------------

	const goTab = (tab, label) => btn(label, `data-tab="${tab}"`);

	// The dots and labels of the reminder ladder timeline (fixed in the app).
	function slaMarks(levels) {
		const rows = levels.map((r) => ({
			wait_minutes: r.wait,
			min_severity: r.severity,
			action: r.action,
		}));
		const waits = [...new Set(rows.map((r) => +r.wait_minutes))].sort((a, b) => a - b);
		return waits
			.map((w, i) => {
				const left = waits.length > 1 ? 4 + (92 * i) / (waits.length - 1) : 50;
				const names = rows.filter((r) => +r.wait_minutes === w);
				const hi = names.some((r) => r.min_severity === "High");
				const up = i % 2 === 0;
				const labels = names
					.map((r) => r.action)
					.filter((v, j, arr) => arr.indexOf(v) === j);
				return `<i class="${hi ? "hi" : ""}" style="left:${left}%"></i><b class="${
					up ? "up" : ""
				}" style="left:${left}%">${e(minutes(w))}<small>${e(
					labels.slice(0, 2).join(", ")
				)}</small></b>`;
			})
			.join("");
	}

	const PANELS = {
		autoreply(frm, d) {
			const f = d.autoreply;
			const t = f.today || {};
			return {
				k: __("Step 4"),
				h: __("Auto reply"),
				p: __(
					"When no rep has answered a customer within the set time, the AI answers from the knowledge base, ERPNext and the deal's last quotation, day or night. When the data does not answer it, the customer is told a rep will reply and the rep gets a task. Never in groups."
				),
				body:
					kv([
						[__("Answered today"), t.Sent || 0],
						[__("Holding messages today"), t["Holding Sent"] || 0],
						[__("Dry runs today (test mode)"), (t["Dry Run"] || 0) + (t.Blocked || 0)],
						[__("Failed today"), t.Failed || 0],
						[__("Waiting now"), f.waiting],
					]) +
					box(
						__("Settings"),
						switchRow(frm, __("Reply to customers"), "enable_auto_reply") +
							`<div class="pf-row"><span>${__(
								"When no rep answers within (minutes)"
							)}</span><input type="number" min="0" max="120" data-text="reply_delay_minutes" value="${e(
								field(frm, "reply_delay_minutes")
							)}" style="width:80px"></div>`
					) +
					`<div class="pf-btns">${btn(
						__("All auto replies"),
						`data-list="Periskope Auto Reply"`,
						true
					)}${btn(__("Chats"), `data-list="Periskope Chat"`)}</div>`,
			};
		},
		crm(frm, d) {
			const f = d.crm;
			return {
				k: __("Step 4b"),
				h: __("CRM updates"),
				p: __(
					"On every lead or deal the agent keeps one WhatsApp summary note, notes for what the customer told us, follow-up tasks for the rep, a timeline entry for each AI reply, and the status when the conversation clearly moves on. Won and Lost are only proposed to the owner."
				),
				body:
					kv([
						[__("Notes written today"), f.notes],
						[__("Tasks written today"), f.tasks],
						[__("Status changes today"), f.status_changes],
						[__("Timeline entries today"), f.timeline],
						[__("Dry runs today (test mode)"), f.dry_runs],
					]) +
					`<div class="pf-btns">${btn(
						__("What the agent did"),
						`data-list="Periskope Agent Action"`,
						true
					)}</div>`,
			};
		},
		agent(frm, d) {
			const f = d.agent;
			return {
				k: __("Step 4c"),
				h: __("Learning agent"),
				p: __(
					"Every 15 minutes the agent reads the finished conversations and updates the knowledge base on its own. It suggests changes to tags, group mappings, ignored numbers and a few settings; those wait until an approver says yes."
				),
				body:
					kv([
						[
							__("Last run"),
							`${f.last_run}${f.last_status ? ` (${f.last_status})` : ""}`,
						],
						[__("Waiting for approval"), f.pending],
						[__("Knowledge entries in use"), f.knowledge],
						[__("Test mode"), f.test_mode ? __("On") : __("Off")],
					]) +
					(f.last_error ? `<div class="pf-warn">${e(f.last_error)}</div>` : "") +
					`<div class="pf-btns">${btn(
						__("Suggestions"),
						`data-list="Periskope Suggestion"`,
						true
					)}${btn(__("Knowledge base"), `data-knowledge="1"`)}${btn(
						__("Agent runs"),
						`data-list="Periskope Agent Run"`
					)}</div>`,
			};
		},
		periskope(frm, d) {
			const f = d.periskope;
			const rows = f.phones
				.map(
					(p) =>
						`<tr><td>${e(p.name)}</td><td>${e(p.rep || "")}</td><td>${e(
							p.state
						)}</td></tr>`
				)
				.join("");
			return {
				k: __("Step 1"),
				h: __("Periskope"),
				p: __(
					"Your WhatsApp company numbers, linked in Periskope. The app reads every message; it sends only the AI replies and the reps' replies typed in the CRM (in test mode only to the allowed numbers)."
				),
				body:
					kv([[__("Connection"), f.connection]]) +
					box(
						__("Linked numbers"),
						`<table class="pf-t"><tr><th>${__("Number")}</th><th>${__(
							"Rep"
						)}</th><th>${__("State")}</th></tr>${
							rows || `<tr><td colspan="3">${__("None yet")}</td></tr>`
						}</table>`
					) +
					`<div class="pf-btns">${btn(
						__("Test Connection"),
						`data-call="test_connection"`,
						true
					)}${goTab("tab_setup", __("Open Setup"))}</div>`,
			};
		},
		capture(frm, d) {
			const f = d.capture;
			const events =
				f.events
					.map((v) => `<span class="indicator-pill gray">${e(v)}</span>`)
					.join(" ") || __("None selected");
			return {
				k: __("Step 2"),
				h: __("Capture"),
				p: __(
					"Periskope calls the webhook for every message. If a call is lost, the hourly catch-up reads the chat history and fills the gap."
				),
				body:
					kv([
						[__("Last webhook"), f.last_webhook],
						[__("Messages today"), f.today],
						[__("Recovered by catch-up (24 h)"), f.recovered],
						[__("Unprocessed logs"), f.unprocessed],
					]) +
					box(
						__("Integration"),
						switchRow(frm, __("Enable Periskope Integration"), "enabled")
					) +
					box(__("Events asked from Periskope"), events) +
					`<div class="pf-btns">${btn(
						__("Create Webhook in Periskope"),
						`data-call="setup_webhooks"`,
						true
					)}${goTab("tab_setup", __("Open Setup"))}${btn(
						__("Webhook logs"),
						`data-list="Periskope Webhook Log"`
					)}</div>`,
			};
		},
		link(frm, d) {
			const c = d.link.counts;
			return {
				k: __("Step 3"),
				h: __("Link to CRM"),
				p: __(
					"Each message is matched to a lead or deal by the customer's mobile number. Groups are matched by name, sender or the customer the AI finds in the text. Messages the AI marks as mattering show on the record's timeline."
				),
				body:
					kv([
						[__("Linked"), c.Linked],
						[__("Internal team groups"), c.Internal],
						[__("Ambiguous"), c.Ambiguous],
						[__("No match"), c["No Match"]],
					]) +
					`<div class="pf-btns">${btn(
						__("Unmatched Numbers report"),
						`data-report="Periskope Unmatched Numbers"`
					)}</div>`,
			};
		},
		queue(frm, d) {
			const f = d.queue;
			return {
				k: __("Step 3b"),
				h: __("Contact queue"),
				p: __(
					"A number that is not on any lead, deal or contact waits here. The AI makes a lead itself when the message is a new business enquiry; suppliers and employees are ignored on their own. The rest can be linked or ignored here."
				),
				body:
					kv([
						[__("New"), f.new],
						[__("Lead created"), f.lead_created],
						[__("Ignored"), f.ignored],
					]) +
					`<div class="pf-btns">${btn(
						__("Open the queue"),
						`data-list="Periskope Contact"`,
						true
					)}</div>`,
			};
		},
		files(frm, d) {
			const f = d.files;
			return {
				k: __("Step 2b"),
				h: __("Attachments"),
				p: __(
					"Every image, document, video and voice note is saved in Frappe and attached to the lead or deal, so it shows in the CRM's Attachments. Images and PDFs are read by the AI: what it is, and its key figures."
				),
				body: kv([
					[__("Saved today"), f.saved],
					[__("Read by the AI today"), f.read],
					[__("Still to save (24 h)"), f.waiting],
				]),
			};
		},
		ai(frm, d) {
			const f = d.ai;
			return {
				k: __("Step 4"),
				h: __("AI reads"),
				p: __(
					"Every customer message goes to the AI as it arrives, with images, PDFs and voice notes, in 1-to-1 chats and groups. It returns a tag, a severity, the mood, a summary and what to do next."
				),
				body:
					kv([
						[__("Model"), f.model],
						[__("Waiting to be tagged"), f.pending],
						[__("Failed"), f.failed],
						[__("Tagged"), f.tagged],
						[__("Corrected by managers"), f.corrected],
						[__("Tokens this month"), f.tokens],
					]) +
					`<div class="pf-btns">${btn(
						__("Test AI Connection"),
						`data-call="test_ai_connection"`,
						true
					)}${btn(
						__("Tagging Review"),
						`data-report="Periskope Tagging Review"`
					)}</div>`,
			};
		},
		tags(frm, d) {
			const f = d.tags;
			const rows = f.tags
				.map(
					(t) =>
						`<tr><td>${e(t.name)}${
							t.default ? ` <small>(${__("default")})</small>` : ""
						}</td><td class="c">${t.messages}</td><td class="c">${sw(
							t.active,
							`data-tag-active="${e(t.name)}"`
						)}</td></tr>`
				)
				.join("");
			return {
				k: __("Step 5"),
				h: __("Tags"),
				p: __(
					"The names the AI can give a message. The description decides what the AI puts under each. Switches here save at once."
				),
				body:
					kv([
						[__("Active tags"), f.tags.filter((t) => t.active).length],
						[__("Default tag"), f.default],
					]) +
					box(
						__("Tags"),
						`<table class="pf-t"><tr><th>${__("Tag")}</th><th class="c">${__(
							"Messages"
						)}</th><th class="c">${__("On")}</th></tr>${rows}</table>`
					) +
					`<div class="pf-btns">${btn(
						__("Add a tag"),
						`data-new="Periskope Tag"`,
						true
					)}${btn(__("All tags"), `data-list="Periskope Tag"`)}</div>`,
			};
		},
		decisions(frm, d) {
			const f = d.decisions;
			return {
				k: __("Step 6"),
				h: __("AI decides"),
				p: __(
					"No rules: the AI decides for each message. Work handed out in a group becomes a CRM Task on the customer's deal for the person mentioned; a problem goes to the managers or the MD at once; a new business enquiry becomes a lead; what matters shows on the record's timeline."
				),
				body:
					kv([
						[__("Tasks created today"), f.tasks],
						[__("Escalated to managers today"), f.managers],
						[__("Escalated to the MD today"), f.md],
						[__("Leads created today"), f.leads],
						[__("Shown on timelines today"), f.timeline],
					]) +
					`<div class="pf-btns">${btn(
						__("What the agent did"),
						`data-list="Periskope Agent Action"`,
						true
					)}</div>`,
			};
		},
		waiting(frm, d) {
			const t = (name) => String(field(frm, name) || "").slice(0, 5);
			const days = field(frm, "working_days") || "Monday to Saturday";
			const select = `<select data-select="working_days">${[
				"Monday to Saturday",
				"Monday to Friday",
				"Every day",
			]
				.map((o) => `<option${o === days ? " selected" : ""}>${o}</option>`)
				.join("")}</select>`;
			return {
				k: __("Step 7"),
				h: __("Customer waiting?"),
				p: __(
					"A customer message that needs a reply and has no answer starts the clock. Only working hours count."
				),
				body:
					kv([[__("Waiting now"), d.waiting.waiting]]) +
					box(
						__("Working hours"),
						`<div class="pf-row"><span>${__(
							"From"
						)}</span><input type="time" step="60" data-time="working_hours_start" value="${e(
							t("working_hours_start")
						)}" style="width:110px"></div><div class="pf-row"><span>${__(
							"To"
						)}</span><input type="time" step="60" data-time="working_hours_end" value="${e(
							t("working_hours_end")
						)}" style="width:110px"></div><div class="pf-row"><span>${__(
							"Days"
						)}</span><span style="width:60%">${select}</span></div>`
					),
			};
		},
		sla(frm, d) {
			const rows = d.sla.levels
				.map(
					(r) =>
						`<tr><td>${e(minutes(r.wait))}</td><td>${e(r.severity)}</td><td>${e(
							r.action
						)}</td></tr>`
				)
				.join("");
			return {
				k: __("Step 8"),
				h: __("Reminder ladder"),
				p: __(
					"What happens as a customer keeps waiting, in working time. It is fixed in the app; with auto reply on, the AI has usually answered long before."
				),
				body:
					kv([
						[__("Open reminders"), d.sla.open],
						[__("At level 2 or 3"), d.sla.deep],
					]) +
					box(
						__("Ladder"),
						`<div class="pf-tl">${slaMarks(
							d.sla.levels
						)}</div><table class="pf-t"><tr><th>${__("Wait")}</th><th>${__(
							"From"
						)}</th><th>${__("Action")}</th></tr>${rows}</table>`
					),
			};
		},
		people(frm, d) {
			const f = d.people;
			const area = (name, label) =>
				`<div style="margin-top:6px"><label class="text-muted" style="font-size:12px">${e(
					label
				)}</label><textarea data-text="${name}">${e(
					field(frm, name) || ""
				)}</textarea></div>`;
			return {
				k: __("Step 9"),
				h: __("People told"),
				p: __(
					"Everyone the app contacts: the rep gets a bell and a task, managers and the MD get the AI's escalations by email."
				),
				body:
					kv([
						[__("Emails today"), f.emails_today],
						[__("Outgoing email"), f.mail_ready ? __("Configured") : __("Not set up")],
					]) +
					(f.mail_ready
						? ""
						: `<div class="pf-warn">${__(
								"Without an outgoing Email Account no alert email is sent."
						  )}</div>`) +
					box(
						__("Lists"),
						area("alert_emails", __("Managers")) +
							area("escalation_emails", __("MD (with the managers)"))
					) +
					`<div class="pf-btns">${
						f.mail_ready
							? ""
							: btn(__("Email Accounts"), `data-list="Email Account"`, true)
					}${goTab("tab_setup", __("Open Setup"))}</div>`,
			};
		},
	};

	// ---- rendering and events ------------------------------------------------------------------
	//
	// The dialog is built once. After that only the part that changed is touched, so a switch or an
	// edit does not redraw (and flicker) the whole diagram.

	// Build the whole dialog: legend, diagram and the panel of the selected step.
	function render(frm) {
		const st = frm._pf;
		const $w = st && st.$body;
		if (!$w) return;
		const data = st.data;
		if (!data) {
			$w.html(`<div class="pf"><div class="text-muted">${__("Loading...")}</div></div>`);
			return;
		}
		st.wide = $w[0].clientWidth >= WIDE_AT;
		const dot = (color, label) => `<span><i style="background:${color}"></i>${label}</span>`;
		$w.html(`<div class="pf${st.wide ? " wide" : ""}"><div>
<div class="pf-legend">${dot("var(--green-500,#2f9e5c)", __("Working"))}${dot(
			"var(--orange-500,#d98b00)",
			__("Needs a look")
		)}${dot("var(--red-500,#d6433a)", __("Not working"))}${dot(
			"var(--gray-400,#aaa)",
			__("Off")
		)}<span>${__(
			"Click a step to see it and change it. Drag to move, Ctrl + scroll to zoom."
		)}</span><a href="#" data-refresh="1">${__("Refresh figures")}</a></div>
<div class="pf-canvas"><div class="pf-view">${drawSvg(data, st.selected)}</div>
<div class="pf-zoom"><button type="button" data-z="in" title="${e(
			__("Zoom in")
		)}">+</button><button type="button" data-z="out" title="${e(
			__("Zoom out")
		)}">−</button><button type="button" data-z="fit" title="${e(
			__("Fit to view")
		)}">⤢</button></div></div></div>
<aside class="pf-panel"></aside></div>`);
		const root = $w[0];
		bindView(frm, root.querySelector(".pf-canvas"));
		root.querySelectorAll("[data-node]").forEach((n) => {
			const pick = () => !frm._pf.dragged && selectNode(frm, n.dataset.node);
			n.addEventListener("click", pick);
			n.addEventListener("keydown", (ev) => {
				if (ev.key === "Enter" || ev.key === " ") {
					ev.preventDefault();
					pick();
				}
			});
		});
		on(root, "[data-refresh]", "click", (a, ev) => {
			ev.preventDefault();
			load(frm);
		});
		renderPanel(frm);
		watchWidth(frm, root);
	}

	// Redraw only the panel of the selected step.
	function renderPanel(frm) {
		const st = frm._pf;
		const aside = st.$body && st.$body[0].querySelector(".pf-panel");
		if (!aside || !st.data) return;
		const panel = PANELS[st.selected](frm, st.data);
		aside.innerHTML = `<div class="kick">${e(panel.k)}</div><h3>${e(panel.h)}</h3><p>${e(
			panel.p
		)}</p>${panel.body}`;
		bind(frm, aside);
	}

	function selectNode(frm, id) {
		const st = frm._pf;
		st.selected = id;
		st.$body[0]
			.querySelectorAll("[data-node]")
			.forEach((n) => n.classList.toggle("sel", n.dataset.node === id));
		renderPanel(frm);
	}

	// Update the health bar and the figure of each card, leaving the rest of the dialog alone.
	function updateFigures(frm) {
		const st = frm._pf;
		const root = st.$body && st.$body[0];
		if (!root || !st.data || !root.querySelector("svg")) return render(frm);
		NODES.forEach((n) => {
			const g = root.querySelector(`[data-node="${n.id}"]`);
			if (!g) return;
			const status = (st.data[n.id] || {}).status || "ok";
			["ok", "warn", "bad", "off"].forEach((c) => g.classList.toggle(c, c === status));
			const m = g.querySelector("[data-metric]");
			const text = nodeMetric(n.id, st.data);
			if (m && m.textContent !== text) m.textContent = text; // leave an unchanged figure alone
		});
	}

	// A wide dialog has room for the panel beside the diagram; a narrow one puts it under.
	function watchWidth(frm, el) {
		if (frm._pf.watch || !window.ResizeObserver) return;
		frm._pf.watch = new ResizeObserver(() => {
			if (frm._pf.data && el.clientWidth >= WIDE_AT !== !!frm._pf.wide) render(frm);
		});
		frm._pf.watch.observe(el);
	}

	// Fetch the figures. The first time (or after a layout change) the dialog is built; after that
	// only the cards are updated.
	// `then` is "panel" to redraw the panel too (new rows, a refused change), otherwise only the cards.
	function load(frm, then) {
		frm._pf = frm._pf || { selected: "periskope", data: null };
		frappe.call({
			method: "periskope_connector.api.settings.get_flow",
			callback(r) {
				frm._pf.data = r.message;
				updateFigures(frm);
				if (then === "panel") renderPanel(frm);
			},
		});
	}

	function openFlow(frm) {
		ensureCss();
		frm._pf = frm._pf || { selected: "periskope", data: null };
		const st = frm._pf;
		if (!st.dialog) {
			const d = new frappe.ui.Dialog({
				title: __("How a message moves"),
				size: "extra-large",
				fields: [{ fieldtype: "HTML", fieldname: "flow" }],
			});
			d.$wrapper.addClass("pf-dialog");
			d.onhide = () => {
				frappe.ui.form.is_saving = false;
				if (st.watch) st.watch.disconnect();
				st.watch = null;
				if (st.changed) {
					st.changed = false;
					frm.reload_doc();
				}
			};
			st.dialog = d;
			st.$body = d.fields_dict.flow.$wrapper;
		}
		st.dialog.show();
		// Frappe reloads an open form when the server says its document changed. While the dialog is
		// open that would redraw the form behind it on every change, so tell Frappe a save is in
		// progress; the form is reloaded once when the dialog closes.
		frappe.ui.form.is_saving = true;
		st.data = null;
		st.view = null; // fit the diagram again each time the dialog opens
		render(frm); // "Loading..." until the figures arrive; load() then builds the dialog
		load(frm);
	}

	// Every change goes straight to the server, one call at a time. The settings form behind the dialog
	// is left alone (it would redraw, and show through the dimmed page); it is reloaded once, when the
	// dialog closes.
	function save(frm, method, args, then) {
		const st = frm._pf;
		st.changed = true;
		st.saving = (st.saving || Promise.resolve()).then(
			() =>
				new Promise((resolve) => {
					frappe.call({
						method: `periskope_connector.api.settings.${method}`,
						args,
						callback() {
							frappe.show_alert({ message: __("Saved"), indicator: "green" }, 2);
							load(frm, then);
							resolve();
						},
						// the server refused the change: show what is really saved
						error() {
							load(frm, "panel");
							resolve();
						},
					});
				})
		);
		return st.saving;
	}

	function setValue(frm, name, value) {
		frm._pf.data.settings[name] = value;
		return save(frm, "set_flow_setting", { field: name, value });
	}

	// Show a switch in its new state straight away; the save follows.
	function flip(el, isOn) {
		el.classList.toggle("on", !!isOn);
		el.setAttribute("aria-pressed", isOn ? "true" : "false");
	}

	// Leave the dialog before going to another page or tab.
	function leave(frm, go) {
		frm._pf.dialog.hide();
		go();
	}

	function on(root, selector, event, fn) {
		root.querySelectorAll(selector).forEach((el) =>
			el.addEventListener(event, (ev) => fn(el, ev))
		);
	}

	// Attach the controls inside `root` (the panel).
	function bind(frm, root) {
		on(root, "[data-field]", "click", (b) => {
			const next = field(frm, b.dataset.field) ? 0 : 1;
			flip(b, next);
			setValue(frm, b.dataset.field, next);
		});
		on(root, "[data-select]", "change", (s) => setValue(frm, s.dataset.select, s.value));
		on(
			root,
			"[data-time]",
			"change",
			(s) => s.value && setValue(frm, s.dataset.time, s.value + ":00")
		);
		on(root, "[data-text]", "change", (s) => setValue(frm, s.dataset.text, s.value));
		on(root, "[data-tag-active]", "click", (b) => {
			const turnOn = !b.classList.contains("on");
			flip(b, turnOn);
			frappe.call({
				method: "periskope_connector.api.settings.set_tag_active",
				args: { tag: b.dataset.tagActive, active: turnOn ? 1 : 0 },
				callback: () => load(frm),
				error: () => flip(b, !turnOn),
			});
		});
		on(root, "[data-tab]", "click", (b) =>
			leave(frm, () =>
				$(frm.wrapper)
					.find(`.form-tabs .nav-link[data-fieldname="${b.dataset.tab}"]`)
					.trigger("click")
			)
		);
		on(root, "[data-list]", "click", (b) =>
			leave(frm, () => frappe.set_route("List", b.dataset.list))
		);
		on(root, "[data-report]", "click", (b) =>
			leave(frm, () => frappe.set_route("query-report", b.dataset.report))
		);
		on(root, "[data-knowledge]", "click", () => leave(frm, () => periskope.open_knowledge()));
		on(root, "[data-page]", "click", (b) =>
			leave(frm, () => frappe.set_route(b.dataset.page))
		);
		on(root, "[data-new]", "click", (b) => leave(frm, () => frappe.new_doc(b.dataset.new)));
		on(root, "[data-open]", "click", (a, ev) => {
			ev.preventDefault();
			const [doctype, name] = a.dataset.open.split("|");
			leave(frm, () => frappe.set_route("Form", doctype, name));
		});
		on(root, "[data-call]", "click", (b) =>
			(frm._pf.saving || Promise.resolve()).then(() =>
				frappe.call({
					method: `periskope_connector.api.settings.${b.dataset.call}`,
					freeze: true,
					callback(r) {
						frappe.show_alert({ message: __("Done"), indicator: "green" });
						if (r.message && r.message.model)
							frappe.show_alert({
								message: __("AI connected ({0})", [r.message.model]),
								indicator: "green",
							});
						load(frm);
					},
				})
			)
		);
	}

	frappe.ui.form.on("Periskope Settings", {
		refresh(frm) {
			if (frm.is_new()) return;
			frm.add_custom_button(__("Flow"), () => openFlow(frm))
				.removeClass("btn-default")
				.addClass("btn-primary");
			// the workspace's Flow shortcut opens the settings and asks for the dialog
			if (window.periskope && periskope.open_flow_next) {
				periskope.open_flow_next = false;
				openFlow(frm);
			} else if (new URLSearchParams(window.location.search).get("flow") === "1") {
				window.history.replaceState(null, "", window.location.pathname);
				openFlow(frm);
			}
		},
		after_save(frm) {
			if (frm._pf && frm._pf.dialog && frm._pf.dialog.display) load(frm);
		},
	});
})();
