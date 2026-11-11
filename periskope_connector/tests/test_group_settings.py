"""Group settings from the inbox (api/group_settings.py): read from Periskope, changed by managers only,
in test mode only for a group of test numbers (and only test numbers added), and a new name kept on the
app's chat. Periskope is never called for real."""

from unittest.mock import patch

import frappe

from periskope_connector.api import group_settings as gs
from periskope_connector.tests.test_compose import _user
from periskope_connector.tests.test_crm_panel import CUSTOMER, A, PanelCase
from periskope_connector.tests.utils import settings

GROUP = "120363000000099999@g.us"
OUTSIDER = "919600066661"


class GroupCase(PanelCase):
	def setUp(self):
		super().setUp()
		settings(
			enabled=1, test_mode=1, test_allowed_chats=CUSTOMER, base_url="https://api.periskope.test/v1"
		)
		self.chat = self._msg()
		frappe.db.set_value(
			"Periskope Chat", self.chat, {"chat_id": GROUP, "is_group": 1, "chat_name": "Old name"}
		)
		self.members = {A: True, CUSTOMER: False}
		self.calls = []

		def fake(method, path, phone=None, json=None, **kw):
			self.calls.append((method, path, phone, json))
			if method == "GET":
				return {
					"chat_id": GROUP,
					"chat_name": "Old name",
					"group_description": "Yarn buyers",
					"messages_admins_only": True,
					"invite_link": "https://chat.whatsapp.com/abc",
					"members": {
						f"{n}@c.us": {"contact_id": f"{n}@c.us", "contact_name": "", "is_admin": admin}
						for n, admin in self.members.items()
					},
				}
			if path.endswith("/settings"):
				return {"chat_id": GROUP, "chat_name": json.get("name") or "Old name"}
			if path.endswith(("/add", "/remove", "/promote", "/demote")):
				return {f"{n}@c.us": {"is_success": True} for n in json["participants"]}
			if path.endswith("/invite"):
				return {"invite_link": "https://chat.whatsapp.com/new"}
			return {}

		p = patch.object(gs, "request", side_effect=fake)
		p.start()
		self.addCleanup(p.stop)

	def _changes(self):
		return [c for c in self.calls if c[0] == "POST"]


class TestRead(GroupCase):
	def test_the_group_is_read_with_its_members_and_our_admin_role(self):
		info = gs.info(self.chat)
		self.assertEqual((info["description"], info["messages_admins_only"]), ("Yarn buyers", True))
		self.assertTrue(info["we_are_admin"])
		self.assertTrue(info["can_change"])
		self.assertEqual(self.calls[0][:3], ("GET", f"chats/{GROUP}", A))

	def test_a_chat_that_is_not_a_group_is_refused(self):
		other = self._msg(contact="919600066662")
		with self.assertRaises(frappe.ValidationError):
			gs.info(other)


class TestChange(GroupCase):
	def test_a_manager_renames_it_and_the_app_keeps_the_name(self):
		gs.update(self.chat, name="Kongu Buyers", messages_admins_only=0, ephemeral="604800")
		body = self._changes()[0][3]
		self.assertEqual(body, {"name": "Kongu Buyers", "messagesAdminsOnly": False, "ephemeral": 604800})
		self.assertEqual(frappe.db.get_value("Periskope Chat", self.chat, "chat_name"), "Kongu Buyers")

	def test_reps_cannot_change_it(self):
		rep = _user("pkt.group.rep@example.com", "Sales User")
		frappe.db.set_value("Periskope Chat", self.chat, "_assign", frappe.as_json([rep]))
		frappe.set_user(rep)
		self.assertFalse(gs.info(self.chat)["can_change"])
		with self.assertRaises(frappe.PermissionError):
			gs.update(self.chat, name="x")
		self.assertEqual(self._changes(), [])

	def test_test_mode_keeps_customers_groups_and_outsiders_out(self):
		with self.assertRaises(frappe.ValidationError):
			gs.members(self.chat, "add", [OUTSIDER])
		self.members[OUTSIDER] = False  # a member who is not a test number
		self.assertTrue(gs.info(self.chat)["blocked"])
		with self.assertRaises(frappe.ValidationError):
			gs.update(self.chat, description="x")
		self.assertEqual(self._changes(), [])

	def test_members_are_promoted_and_removed_but_never_our_number(self):
		out = gs.members(self.chat, "promote", frappe.as_json([CUSTOMER]))
		self.assertTrue(out["results"][0]["ok"])
		self.assertEqual(self._changes()[-1][1:], (f"chats/{GROUP}/promote", A, {"participants": [CUSTOMER]}))
		with self.assertRaises(frappe.ValidationError):
			gs.members(self.chat, "remove", [A])

	def test_a_new_invite_link(self):
		self.assertEqual(gs.reset_invite(self.chat)["invite_link"], "https://chat.whatsapp.com/new")


class TestCreate(GroupCase):
	def _create(self, people, **kw):
		with patch.object(
			gs, "request", return_value={"chat_id": "120363000000077777@g.us", "chat_name": "New"}
		) as req:
			out = gs.create(A, "Kongu Buyers", frappe.as_json(people), **kw)
		return out, req

	def test_a_manager_makes_a_group_of_test_numbers_and_it_opens_in_the_inbox(self):
		out, req = self._create([CUSTOMER, A])  # our own number is left out: it makes the group
		args = req.call_args
		self.assertEqual(args.args[:2], ("POST", "chats/create"))
		self.assertEqual(args.kwargs["json"]["participants"], [CUSTOMER])
		self.assertTrue(args.kwargs["json"]["options"]["infoAdminsOnly"])
		row = frappe.db.get_value(
			"Periskope Chat", out["chat"], ["is_group", "chat_name", "whatsapp_number"], as_dict=True
		)
		self.assertEqual((row.is_group, row.chat_name, row.whatsapp_number), (1, "Kongu Buyers", A))

	def test_a_local_number_gets_the_country_code(self):
		local = CUSTOMER[-10:]
		_, req = self._create([local])
		self.assertEqual(req.call_args.kwargs["json"]["participants"], [CUSTOMER])

	def test_test_mode_keeps_customers_out_and_reps_cannot(self):
		with self.assertRaises(frappe.ValidationError):
			self._create([OUTSIDER])
		frappe.set_user(_user("pkt.group.rep2@example.com", "Sales User"))
		with self.assertRaises(frappe.PermissionError):
			self._create([CUSTOMER])


class TestJoinLeave(GroupCase):
	def test_a_manager_joins_by_link_and_the_group_opens_in_the_inbox(self):
		joined = {"chat_id": "120363000000088888@g.us", "chat_name": "Buyers"}
		with patch.object(gs, "request", return_value=joined) as req:
			# test mode: who is in the group cannot be known before joining, so it is refused
			self.assertRaises(frappe.ValidationError, gs.join, A, "https://chat.whatsapp.com/AbC123xyz")
			req.assert_not_called()
		settings(test_mode=0)
		with patch.object(gs, "request", return_value=joined) as req:
			out = gs.join(A, "https://chat.whatsapp.com/invite/AbC123xyz?x=1")
		self.assertEqual(req.call_args.args[:2], ("POST", "chats/accept-invite"))
		self.assertEqual(req.call_args.kwargs["json"], {"invite_code": "AbC123xyz"})
		self.assertEqual(frappe.db.get_value("Periskope Chat", out["chat"], "chat_name"), "Buyers")
		with self.assertRaises(frappe.ValidationError):
			gs.join(A, "not a link!")

	def test_leaving_closes_the_chat_and_logs_it(self):
		gs.leave(self.chat)
		self.assertIn(("POST", f"chats/{GROUP}/leave", A, None), self.calls)
		self.assertEqual(frappe.db.get_value("Periskope Chat", self.chat, "status"), "Closed")
		self.assertTrue(frappe.db.exists("Periskope Chat Activity", {"chat": self.chat, "action": "Group"}))

	def test_leaving_a_customers_group_is_refused_in_test_mode(self):
		self.members[OUTSIDER] = False
		with self.assertRaises(frappe.ValidationError):
			gs.leave(self.chat)
