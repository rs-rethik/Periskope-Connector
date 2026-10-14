// The admins' chat with the agent: build the knowledge base by talking or uploading a document,
// and ask about what the agent did. Changes are shown as a preview and saved only on Confirm.
frappe.pages["periskope-assistant"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Periskope Assistant"),
		single_column: true,
	});
	new PeriskopeAssistant(page);
};

class PeriskopeAssistant {
	constructor(page) {
		this.page = page;
		this.history = [];
		this.file_url = null;
		this.render();
		this.say(
			"assistant",
			__(
				'Tell me what customers should know, for example: "Our delivery time for Chennai is 2 days, other states 5 days." You can also upload a price list or FAQ, or ask why the agent replied the way it did, what it learned this week, or what it cannot answer yet.'
			)
		);
	}

	render() {
		const e = frappe.utils.escape_html;
		this.$root = $(`
			<div class="pa">
				<style>
					.pa{max-width:900px;margin:0 auto}
					.pa-log{min-height:380px;max-height:62vh;overflow-y:auto;padding:12px;border:1px solid var(--border-color);border-radius:8px;background:var(--card-bg)}
					.pa-msg{margin:8px 0;display:flex}
					.pa-msg .b{padding:9px 13px;border-radius:12px;max-width:80%;white-space:pre-wrap;line-height:1.45}
					.pa-msg.admin{justify-content:flex-end}.pa-msg.admin .b{background:var(--primary);color:#fff}
					.pa-msg.assistant .b{background:var(--control-bg)}
					.pa-card{border:1px solid var(--border-color);border-radius:8px;padding:10px 12px;margin:6px 0;background:var(--card-bg)}
					.pa-card .op{font-size:11px;font-weight:600;text-transform:uppercase;color:var(--text-muted)}
					.pa-card .old{text-decoration:line-through;color:var(--text-muted)}
					.pa-in{display:flex;gap:8px;margin-top:8px}
					.pa-quick{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
				</style>
				<div class="pa-log"></div>
				<div class="pa-quick"></div>
				<textarea class="form-control pa-text" style="margin-top:10px;min-height:56px" placeholder="${e(
					__("Type a message, Enter to send, Shift+Enter for a new line")
				)}"></textarea>
				<div class="pa-in">
					<button class="btn btn-primary btn-sm pa-send">${e(__("Send"))}</button>
					<button class="btn btn-default btn-sm pa-attach">${e(__("Upload a document"))}</button>
				</div>
				<div class="pa-file text-muted small" style="margin-top:4px"></div>
			</div>`).appendTo(this.page.main);
		this.$log = this.$root.find(".pa-log");
		this.$input = this.$root.find(".pa-text");
		[
			__("What can't you answer yet?"),
			__("Which conversations were escalated today?"),
			__("What tags did you learn this week?"),
			__("How are you set up?"),
		].forEach((q) =>
			$(`<button class="btn btn-xs btn-default">${e(q)}</button>`)
				.on("click", () => this.send(q))
				.appendTo(this.$root.find(".pa-quick"))
		);
		this.$root.find(".pa-send").on("click", () => this.send());
		this.$input.on("keydown", (ev) => {
			if (ev.key === "Enter" && !ev.shiftKey) {
				ev.preventDefault();
				this.send();
			}
		});
		this.$root.find(".pa-attach").on("click", () => {
			new frappe.ui.FileUploader({
				allow_multiple: false,
				make_attachments_public: false,
				restrictions: { allowed_file_types: [".pdf", ".xlsx", ".csv", ".txt", ".docx"] },
				on_success: (file) => {
					this.file_url = file.file_url;
					this.$root
						.find(".pa-file")
						.text(__("Attached: {0}. Send a message to read it.", [file.file_name]));
				},
			});
		});
	}

	say(role, text) {
		const e = frappe.utils.escape_html;
		this.$log.append(`<div class="pa-msg ${role}"><div class="b">${e(text)}</div></div>`);
		this.$log.scrollTop(this.$log[0].scrollHeight);
	}

	preview(changes, token) {
		const e = frappe.utils.escape_html;
		const labels = { Add: __("New entry"), Modify: __("Change"), Remove: __("Retire") };
		const $box = $(`<div class="pa-preview"></div>`);
		changes.forEach((c) => {
			const before =
				c.operation === "Modify" && c.before
					? `<div class="old">${e(c.before.question)}: ${e(c.before.answer)}</div>`
					: "";
			$box.append(`<div class="pa-card"><div class="op">${e(
				labels[c.operation]
			)}</div>${before}
				<div><b>${e(c.question)}</b></div><div>${e(c.answer)}</div></div>`);
		});
		const $btns = $(`<div style="margin:6px 0 12px"></div>`).appendTo($box);
		$(`<button class="btn btn-primary btn-sm">${e(__("Confirm and save"))}</button>`)
			.on("click", () =>
				frappe.call({
					method: "periskope_connector.kb_chat.confirm",
					args: { token },
					freeze: true,
					callback: (r) => {
						$btns.remove();
						this.say(
							"assistant",
							__("Saved {0} change(s) to the knowledge base.", [r.message.saved])
						);
					},
				})
			)
			.appendTo($btns);
		$(
			`<button class="btn btn-default btn-sm" style="margin-left:6px">${e(
				__("Discard")
			)}</button>`
		)
			.on("click", () => {
				$btns.remove();
				this.say("assistant", __("Discarded. Nothing was saved."));
			})
			.appendTo($btns);
		this.$log.append($box);
		this.$log.scrollTop(this.$log[0].scrollHeight);
	}

	send(text) {
		const message = (text || this.$input.val() || "").trim();
		if (!message) return;
		this.$input.val("");
		this.say("admin", message);
		const file_url = this.file_url;
		this.file_url = null;
		this.$root.find(".pa-file").text("");
		frappe.call({
			method: "periskope_connector.kb_chat.chat",
			args: { message, history: JSON.stringify(this.history), file_url },
			freeze: true,
			freeze_message: __("Thinking..."),
			callback: (r) => {
				const out = r.message || {};
				this.history.push({ role: "admin", text: message });
				this.history.push({ role: "assistant", text: out.reply || "" });
				this.say("assistant", out.reply || __("(no reply)"));
				if (out.preview && out.preview.length && out.token)
					this.preview(out.preview, out.token);
			},
		});
	}
}
