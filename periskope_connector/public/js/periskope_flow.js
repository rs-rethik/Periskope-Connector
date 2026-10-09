// The Flow dialog of Periskope Settings: the pipeline as a diagram, with an edit panel for each step.
// Every change made in the dialog is saved as it is made: switches, SLA rows, emails and so on save the
// settings form, and tags and their actions (separate records) save through their own calls.

(() => {
	const WIDE_AT = 1120; // px of dialog width from which the panel sits to the right
	const COL = { A: 24, B: 272, C: 520, D: 768 };
	const ROW = { 1: 60, 2: 255, 3: 450 };
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
			id: "keywords",
			col: "A",
			row: 2,
			title: __("Keyword rules"),
			sub: __("Words that always alert"),
		},
		{ id: "ai", col: "B", row: 2, title: __("AI tagging"), sub: __("Customer text only") },
		{
			id: "tags",
			col: "C",
			row: 2,
			title: __("Tags"),
			sub: __("What the AI can call a message"),
		},
		{
			id: "actions",
			col: "D",
			row: 2,
			title: __("Tag actions"),
			sub: __("What each tag triggers"),
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
			title: __("SLA ladder"),
			sub: __("bell, ToDo, email, owner"),
		},
		{ id: "people", col: "D", row: 3, title: __("People told"), sub: __("Email and bell") },
	];
	const EDGES = [
		["M224 105H272"],
		["M472 105H520"],
		["M720 105H768", "dash", __("no match"), 744, 97, "middle"],
		["M372 150V253"],
		["M372 150V190H124V253", "", __("every message"), 132, 184],
		["M472 300H520"],
		["M720 300H768"],
		["M372 345V448", "", __("needs a reply"), 380, 400],
		["M472 495H520"],
		["M720 495H768"],
		["M868 345V448", "", __("alert, ToDo"), 876, 400],
		["M868 255V190H620V152", "dash", __("lead, timeline comment"), 636, 184],
		["M124 345V582H868V552", "dash", __("keyword match"), 140, 576],
	];
	const ACTION_LABELS = {
		"Create Lead": __("Lead"),
		"Email Alert": __("Mail"),
		"Timeline Comment": __("Note"),
		"ToDo for Rep": __("ToDo"),
	};
	const SLA_ACTIONS = [
		"Notify Rep",
		"ToDo for Rep",
		"Email Managers",
		"Email Escalation List",
		"Email Recipients",
	];
	const e = (v) => frappe.utils.escape_html(v == null ? "" : String(v));

	const CSS = `
.pf{display:block;margin:8px 0}
.pf.wide{display:grid;grid-template-columns:minmax(0,1fr) 400px;gap:16px;align-items:start}
.pf.wide .pf-panel{margin-top:0;position:sticky;top:12px}
.pf-note{background:var(--bg-orange,#fdf3d8);color:var(--text-on-orange,#8a5b00);border-radius:6px;padding:7px 12px;font-size:12.5px;margin-bottom:10px}
.pf-legend{display:flex;flex-wrap:wrap;gap:4px 16px;color:var(--text-muted);font-size:12px;margin-bottom:8px;align-items:center}
.pf-legend i{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.pf-canvas{overflow-x:auto;border:1px solid var(--border-color);border-radius:8px;background:var(--control-bg,#f4f5f6)}
.pf svg{display:block;min-width:760px;width:100%;height:auto}
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
	const rowsOf = (frm, table) => frm._pf.data.settings[table] || [];
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

	function drawSvg(data, selected) {
		let s = `<svg viewBox="0 0 1000 610" role="group" aria-label="${e(
			__("How a WhatsApp message moves through the app")
		)}">`;
		s += `<defs><marker id="pf-ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="var(--text-muted)"/></marker></defs>`;
		[20, 215, 410].forEach(
			(y) => (s += `<rect class="band" x="0" y="${y}" width="1000" height="150"/>`)
		);
		[
			[38, __("1 · Capture")],
			[233, __("2 · Understand")],
			[428, __("3 · Follow up")],
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
			if (n.id === "actions") {
				s += [
					["Lead", 18, 44],
					["Mail", 67, 44],
					["Note", 116, 44],
				]
					.map(
						([t, x, w]) =>
							`<rect class="chip" x="${x}" y="56" width="${w}" height="20" rx="10"/><text class="chip-t" x="${
								x + 8
							}" y="70">${e(__(t))}</text>`
					)
					.join("");
				s += `<rect class="chip" x="165" y="56" width="26" height="20" rx="10"/><text class="chip-t" x="171" y="70">+1</text>`;
			} else {
				s += `<text class="m" data-metric="${n.id}" x="18" y="72">${e(
					nodeMetric(n.id, data)
				)}</text>`;
			}
			s += `</g>`;
		});
		return s + `</svg>`;
	}

	// ---- panels -------------------------------------------------------------------------------

	const goTab = (tab, label) => btn(label, `data-tab="${tab}"`);

	// The dots and labels of the SLA ladder timeline, from the rows now in the form.
	function slaMarks(frm) {
		const rows = rowsOf(frm, "sla_levels");
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
					"Your WhatsApp company numbers, linked in Periskope. The app only reads from them and never sends a message."
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
					)}${goTab("tab_connection", __("Open Connection tab"))}</div>`,
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
					)}${goTab("tab_connection", __("Open Connection tab"))}${btn(
						__("Webhook logs"),
						`data-list="Periskope Webhook Log"`
					)}</div>`,
			};
		},
		link(frm, d) {
			const c = d.link.counts;
			const mode = field(frm, "comment_mode") || "One per message";
			const select = `<select data-select="comment_mode">${[
				"One per message",
				"Burst",
				"Off",
			]
				.map((o) => `<option${o === mode ? " selected" : ""}>${o}</option>`)
				.join("")}</select>`;
			return {
				k: __("Step 3"),
				h: __("Link to CRM"),
				p: __(
					"Each message is matched to a lead or deal by the customer's mobile number. Groups are matched by name, sender or the customer the AI finds in the text."
				),
				body:
					kv([
						[__("Linked"), c.Linked],
						[__("Internal team groups"), c.Internal],
						[__("Ambiguous"), c.Ambiguous],
						[__("No match"), c["No Match"]],
					]) +
					box(
						__("Settings"),
						`<div class="pf-row"><span>${__(
							"Timeline comments"
						)}</span><span style="width:55%">${select}</span></div>` +
							switchRow(
								frm,
								__("Reps see their own numbers"),
								"reps_see_own_numbers"
							)
					) +
					`<div class="pf-btns">${btn(
						__("Unmatched Numbers report"),
						`data-report="Periskope Unmatched Numbers"`
					)}${goTab("tab_crm", __("Open CRM Visibility"))}</div>`,
			};
		},
		queue(frm, d) {
			const f = d.queue;
			return {
				k: __("Step 3b"),
				h: __("Contact queue"),
				p: __(
					"A number that is not on any lead, deal or contact waits here. You make a lead, link it to an existing record, or ignore it."
				),
				body:
					kv([
						[__("New"), f.new],
						[__("Lead created"), f.lead_created],
						[__("Ignored"), f.ignored],
					]) +
					box(
						__("Rules"),
						switchRow(
							frm,
							__("Create leads from new enquiries"),
							"create_leads_from_enquiries"
						) +
							switchRow(frm, __("Ignore supplier numbers"), "ignore_suppliers") +
							switchRow(frm, __("Ignore employee numbers"), "ignore_employees")
					) +
					`<div class="pf-btns">${btn(
						__("Open the queue"),
						`data-list="Periskope Contact"`,
						true
					)}${goTab("tab_capture", __("Open Lead Capture"))}</div>`,
			};
		},
		keywords(frm, d) {
			const f = d.keywords;
			const rows = rowsOf(frm, "keyword_rules")
				.map(
					(r) =>
						`<tr><td>${e(r.rule_name)}</td><td><input data-row="keyword_rules|${
							r.idx
						}|recipients" value="${e(r.recipients || "")}" placeholder="${e(
							__("emails, comma separated")
						)}"></td><td class="c">${sw(
							!!r.enabled,
							`data-rowsw="keyword_rules|${r.idx}|enabled"`
						)}</td></tr>`
				)
				.join("");
			const silent = f.rules.some((r) => r.enabled && !r.recipients.trim());
			return {
				k: __("Step 2b"),
				h: __("Keyword rules"),
				p: __(
					"Words that alert people on their own, whatever the AI decides. A rule with no recipients sends nothing."
				),
				body:
					kv([
						[__("Rules"), f.rules.length],
						[__("Alerts today"), f.alerts_today],
					]) +
					box(
						__("Rules"),
						`<table class="pf-t"><tr><th>${__("Rule")}</th><th>${__(
							"Alert to"
						)}</th><th class="c">${__("On")}</th></tr>${rows}</table>${
							silent
								? `<div class="pf-warn">${__(
										"A rule without recipients never sends."
								  )}</div>`
								: ""
						}`
					) +
					`<div class="pf-btns">${goTab(
						"tab_keywords",
						__("Edit words and add rules")
					)}</div>`,
			};
		},
		ai(frm, d) {
			const f = d.ai;
			return {
				k: __("Step 4"),
				h: __("AI tagging"),
				p: __(
					"Only customer text messages go to the AI. It returns a tag, a severity, the mood and a one-line summary. Managers can correct it."
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
					box(
						__("Controls"),
						switchRow(frm, __("Tag new messages with AI"), "enable_classification") +
							switchRow(frm, __("Also tag group chats"), "classify_group_chats")
					) +
					`<div class="pf-btns">${btn(
						__("Test AI Connection"),
						`data-call="test_ai_connection"`,
						true
					)}${goTab("tab_ai", __("Edit AI instructions"))}${btn(
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
		actions(frm, d) {
			const f = d.tags;
			const head = f.action_names
				.map((a) => `<th class="c">${e(ACTION_LABELS[a] || a)}</th>`)
				.join("");
			const rows = f.tags
				.filter((t) => t.active)
				.map(
					(t) =>
						`<tr><td><a href="#" data-open="Periskope Tag|${e(t.name)}">${e(
							t.name
						)}</a></td>${f.action_names
							.map(
								(a) =>
									`<td class="c"><button type="button" class="pf-tick${
										t.actions[a] ? " on" : ""
									}" aria-label="${e(a)}" aria-pressed="${
										t.actions[a]
									}" data-tag-action="${e(t.name)}|${e(a)}"></button></td>`
							)
							.join("")}</tr>`
				)
				.join("");
			return {
				k: __("Step 6"),
				h: __("Tag actions"),
				p: __(
					"What the app does when a message gets a tag. A tick adds the usual row (alerts start at High severity); open a tag to change severity, chat type or recipients. Ticks save at once."
				),
				body:
					box(
						__("Tag by action"),
						`<table class="pf-t"><tr><th>${__("Tag")}</th>${head}</tr>${rows}</table>`
					) +
					`<div class="pf-btns">${btn(
						__("All tags"),
						`data-list="Periskope Tag"`
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
						__("Reply reminders"),
						switchRow(frm, __("Enable Reply Reminders"), "enable_reply_reminders") +
							switchRow(frm, __("Include group chats"), "remind_group_chats")
					) +
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
					) +
					`<div class="pf-btns">${goTab(
						"tab_reminders",
						__("Open Reply Reminders")
					)}</div>`,
			};
		},
		sla(frm, d) {
			const rows = rowsOf(frm, "sla_levels");
			const marks = slaMarks(frm);
			const sel = (name, value, options) =>
				`<select data-row="sla_levels|${name}|${value.field}">${options
					.map((o) => `<option${o === value.v ? " selected" : ""}>${o}</option>`)
					.join("")}</select>`;
			const body = rows
				.map(
					(r) =>
						`<tr><td style="width:70px"><input type="number" min="0" data-row="sla_levels|${
							r.idx
						}|wait_minutes" value="${e(r.wait_minutes)}"></td><td>${sel(
							r.idx,
							{ field: "min_severity", v: r.min_severity },
							["Low", "Medium", "High"]
						)}</td><td>${sel(r.idx, { field: "action", v: r.action }, SLA_ACTIONS)}${
							r.action === "Email Recipients"
								? `<input data-row="sla_levels|${r.idx}|recipients" value="${e(
										r.recipients || ""
								  )}" placeholder="${e(__("emails"))}" style="margin-top:3px">`
								: ""
						}</td><td class="c"><button type="button" class="btn btn-xs btn-default" data-sla-del="${
							r.idx
						}" aria-label="${e(__("Remove"))}">&times;</button></td></tr>`
				)
				.join("");
			return {
				k: __("Step 8"),
				h: __("SLA ladder"),
				p: __(
					"What happens as a customer keeps waiting, in working time. High severity can jump ahead. Changes save as you make them."
				),
				body:
					kv([
						[__("Open reminders"), d.sla.open],
						[__("At level 2 or 3"), d.sla.deep],
					]) +
					box(
						__("Ladder"),
						`<div class="pf-tl">${marks}</div><table class="pf-t"><tr><th>${__(
							"Wait (min)"
						)}</th><th>${__("From")}</th><th>${__(
							"Action"
						)}</th><th></th></tr>${body}</table>`
					) +
					`<div class="pf-btns">${btn(
						__("Add a step"),
						`data-sla-add="1"`,
						true
					)}</div>`,
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
					"Everyone the app contacts: the rep gets a bell and a ToDo, managers and the owner get emails."
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
						area("alert_emails", __("Managers (also the default for email alerts)")) +
							area("escalation_emails", __("Escalation list (the owner)"))
					) +
					`<div class="pf-btns">${
						f.mail_ready
							? ""
							: btn(__("Email Accounts"), `data-list="Email Account"`, true)
					}${goTab("tab_reminders", __("Open Reply Reminders"))}</div>`,
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
			"Click a step to see it and change it. Changes save as you make them."
		)}</span><a href="#" data-refresh="1">${__("Refresh figures")}</a></div>
<div class="pf-canvas">${drawSvg(data, st.selected)}</div></div>
<aside class="pf-panel"></aside></div>`);
		const root = $w[0];
		root.querySelectorAll("[data-node]").forEach((n) => {
			const pick = () => selectNode(frm, n.dataset.node);
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

	function setRow(frm, table, idx, fieldname, value) {
		const row = rowsOf(frm, table).find((r) => String(r.idx) === String(idx));
		if (!row) return;
		row[fieldname] = value;
		if (table === "sla_levels") {
			const tl = frm._pf.$body[0].querySelector(".pf-tl");
			if (tl) tl.innerHTML = slaMarks(frm);
		}
		// choosing "Email Recipients" needs an address box
		save(
			frm,
			"set_flow_row",
			{ table, idx, field: fieldname, value },
			fieldname === "action" ? "panel" : null
		);
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
		on(root, "[data-row]", "change", (i) => {
			const [table, name, fieldname] = i.dataset.row.split("|");
			setRow(frm, table, name, fieldname, i.type === "number" ? +i.value : i.value);
		});
		on(root, "[data-rowsw]", "click", (b) => {
			const [table, idx, fieldname] = b.dataset.rowsw.split("|");
			const row = rowsOf(frm, table).find((r) => String(r.idx) === idx);
			if (!row) return;
			const next = row[fieldname] ? 0 : 1;
			flip(b, next);
			setRow(frm, table, idx, fieldname, next);
		});
		on(root, "[data-sla-del]", "click", (b) =>
			save(frm, "remove_flow_sla_level", { idx: b.dataset.slaDel }, "panel")
		);
		on(root, "[data-sla-add]", "click", () => save(frm, "add_flow_sla_level", {}, "panel"));
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
		on(root, "[data-tag-action]", "click", (b) => {
			const [tag, action] = b.dataset.tagAction.split("|");
			const turnOn = !b.classList.contains("on");
			flip(b, turnOn);
			frappe.call({
				method: "periskope_connector.api.settings.set_tag_action",
				args: { tag, action, on: turnOn ? 1 : 0 },
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
		},
		after_save(frm) {
			if (frm._pf && frm._pf.dialog && frm._pf.dialog.display) load(frm);
		},
	});
})();
