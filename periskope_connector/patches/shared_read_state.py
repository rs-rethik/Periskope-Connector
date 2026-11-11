"""Chats now carry one read state for everyone (read_state.py). A chat someone had already read before
this change keeps counting as read: its read time is the latest time anyone saw it."""

import frappe


def execute():
	frappe.db.sql(
		"""update `tabPeriskope Chat` c
		join (select chat, max(seen_at) seen_at from `tabPeriskope Chat Seen` group by chat) s
			on s.chat = c.name
		set c.read_at = s.seen_at
		where c.read_at is null"""
	)
