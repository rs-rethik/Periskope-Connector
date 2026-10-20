"""Tests for chat_tasks: the work a group message hands out becomes CRM Tasks on the right customer,
for the right person."""

import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_days, get_datetime, getdate, now_datetime

from periskope_connector import chat_tasks, messages
from periskope_connector.ai import classifier
from periskope_connector.tests.utils import GROUP_CHAT, event, isolate, settings

GOWTHAM, VIJAY, SIVA = "9600071001", "9600071002", "9600071003"
BODY = (
	f"@91{GOWTHAM} We Need to Complete the below Work today\n\n1 Zylo Arrow Export GSM Roll Return\n"
	f'2 Zylo Balu Export Sample Ready in 72" Open\n\n@91{VIJAY} Follow with Arun and Need to Send '
	"1300 Kg Mirror DL 160 RFD to Zylofab Sourcing"
)


def _result(tasks):
	return {
		"category": "general",
		"sentiment": "Neutral",
		"severity": "Low",
		"summary": "Work for today",
		"confidence": 90,
		"needs_reply": False,
		"follow_up_on": "",
		"mentioned_products": "",
		"mentioned_amount": "",
		"competitor_name": "",
		"customer_name": "",
		"contact_name": "",
		"language": "English",
		"tasks": tasks,
	}


def _task(customer, task, phone="", due=""):
	return {"customer": customer, "task": task, "assignee_phone": phone, "due_date": due}


TODAY = str(getdate())
TASKS = [
	_task("Zylo Arrow Export", "GSM roll return", f"91{GOWTHAM}", TODAY),
	_task("Zylo Balu Export", 'Get the 72" open sample ready', f"91{GOWTHAM}", TODAY),
	_task("Zylofab Sourcing", "Send 1300 kg Mirror DL 160 RFD (follow with Arun)", f"91{VIJAY}", TODAY),
]


class TestChatTasks(FrappeTestCase):
	def setUp(self):
		isolate(self, "chat_tasks_test")
		settings(test_mode=0, classify_group_chats=1, create_chat_tasks=1, working_hours_end="19:00:00")
		for p in (
			patch.object(messages, "fetch_chat_name", lambda *a, **k: "Zylo Sales Group"),
			patch("periskope_connector.linking.fetch_chat_name", lambda *a, **k: "Zylo Sales Group"),
			patch("periskope_connector.ai.analyse.queue", lambda *a, **k: None),
		):
			p.start()
			self.addCleanup(p.stop)
		self.users = {phone: self._user(phone) for phone in (GOWTHAM, VIJAY, SIVA)}
		self.arrow = self._deal("ZYLO ARROW EXPORTS", owner=self.users[GOWTHAM])
		self.arrow_other = self._deal("ZYLO ARROW EXPORTS", owner="Administrator")
		self.balu = self._deal("ZYLO BALU EXPORTS")
		self.palladam = self._deal("ZYLO BALU EXPORTS - PALLADAM")
		self.sourcing = self._deal("ZYLOFAB SORUCING", owner=self.users[VIJAY])
		self.n = 0

	def _user(self, phone):
		email = f"chat.task.{phone}@example.com"
		if not frappe.db.exists("User", email):
			frappe.get_doc(
				{
					"doctype": "User",
					"email": email,
					"first_name": f"Rep {phone[-2:]}",
					"mobile_no": phone,
					"send_welcome_email": 0,
				}
			).insert(ignore_permissions=True)
		return email

	def _deal(self, organization, owner=None):
		if not frappe.db.exists("CRM Organization", organization):
			frappe.get_doc({"doctype": "CRM Organization", "organization_name": organization}).insert(
				ignore_permissions=True
			)
		doc = frappe.get_doc({"doctype": "CRM Deal", "organization": organization, "deal_owner": owner})
		doc.flags.ignore_mandatory = doc.flags.ignore_links = doc.flags.ignore_validate = True
		doc.insert(ignore_permissions=True)
		return doc.name

	def _message(self, body=BODY):
		self.n += 1
		return messages.create_message(
			event(
				message_id=f"false_{GROUP_CHAT}_TASK{self.n}",
				chat_id=GROUP_CHAT,
				sender_phone=f"91{SIVA}@c.us",
				body=body,
				mentioned_ids=[f"91{GOWTHAM}@c.us", f"91{VIJAY}@c.us"],
			)
		)

	def _classify(self, name, tasks=TASKS):
		classifier.apply_result(frappe.get_doc("Periskope Message", name), _result(tasks))

	def _tasks(self):
		return frappe.get_all(
			"CRM Task",
			filters={"description": ["like", "%Mirror DL 160%"]},
			fields=[
				"title",
				"assigned_to",
				"reference_doctype",
				"reference_docname",
				"due_date",
				"description",
			],
			order_by="creation asc",
		)

	def test_misspelt_and_plural_names_find_the_customer(self):
		self.assertEqual(chat_tasks.organizations_like("Zylofab Sourcing"), ["ZYLOFAB SORUCING"])
		self.assertEqual(chat_tasks.organizations_like("Zylo Arrow Export"), ["ZYLO ARROW EXPORTS"])
		# the exact name beats the longer one that contains it
		self.assertEqual(chat_tasks.organizations_like("zylo balu exports"), ["ZYLO BALU EXPORTS"])
		self.assertEqual(chat_tasks.organizations_like("Quixotic Weaving Mills"), [])

	def test_each_job_becomes_a_task_on_its_deal_for_the_person_mentioned(self):
		self._classify(self._message())
		tasks = self._tasks()
		self.assertEqual(
			[(t.reference_docname, t.assigned_to) for t in tasks],
			[
				# Gowtham's deal of the two Arrow Exports deals
				(self.arrow, self.users[GOWTHAM]),
				(self.balu, self.users[GOWTHAM]),
				(self.sourcing, self.users[VIJAY]),
			],
		)
		self.assertEqual(tasks[2].title, "Send 1300 kg Mirror DL 160 RFD (follow with Arun)")
		self.assertEqual(get_datetime(tasks[0].due_date), get_datetime(f"{TODAY} 19:00:00"))
		self.assertIn("Rep 03", tasks[0].description)  # who gave the task
		self.assertIn("Zylo Sales Group", tasks[0].description)
		# Gowtham sees it in his assignments
		self.assertTrue(
			frappe.db.exists(
				"ToDo", {"reference_type": "CRM Task", "allocated_to": self.users[GOWTHAM], "status": "Open"}
			)
		)

	def test_a_message_makes_its_tasks_once(self):
		name = self._message()
		self._classify(name)
		self.assertEqual(chat_tasks.from_message(name, TASKS), [])
		self.assertEqual(len(self._tasks()), 3)

	def test_an_unknown_customer_still_gets_a_task_that_says_what_to_check(self):
		self._classify(
			self._message(),
			[_task("Quixotic Weaving Mills", "Send the Mirror DL 160 lab dip", f"91{VIJAY}")],
		)
		task = self._tasks()[0]
		self.assertEqual((task.reference_docname, task.assigned_to), (None, self.users[VIJAY]))
		self.assertIn("No customer called Quixotic Weaving Mills", task.description)

	def test_a_number_not_mentioned_is_not_trusted(self):
		self._classify(
			self._message(),
			[_task("Zylofab Sourcing", "Send the Mirror DL 160 stock", f"91{GOWTHAM[:-1]}9")],
		)
		# no such @mention: the deal's owner gets it
		self.assertEqual(self._tasks()[0].assigned_to, self.users[VIJAY])

	def test_without_a_mention_or_customer_the_sender_gets_it(self):
		self._classify(self._message(), [_task("", "Count the Mirror DL 160 rolls in stock")])
		self.assertEqual(self._tasks()[0].assigned_to, self.users[SIVA])

	def test_no_day_means_today_or_tomorrow_after_hours(self):
		due = chat_tasks.due_on("")
		today_end = get_datetime(f"{getdate()} 19:00:00")
		self.assertEqual(due, today_end if now_datetime() < today_end else add_days(today_end, 1))
		self.assertEqual(chat_tasks.due_on("2031-09-02"), get_datetime("2031-09-02 19:00:00"))

	def test_test_mode_and_the_switch_keep_tasks_out_of_the_crm(self):
		settings(test_mode=1, test_allowed_chats="916374955805")
		name = self._message()
		self._classify(name)
		self.assertEqual(self._tasks(), [])
		action = frappe.get_all(
			"Periskope Agent Action",
			filters={"action": "Create Task", "chat_id": GROUP_CHAT},
			fields=["mode", "detail"],
		)
		self.assertEqual(len(action), 3)
		self.assertEqual({a.mode for a in action}, {"Dry Run"})
		self.assertEqual(json.loads(action[0].detail)["message"], name)
		settings(test_mode=0, create_chat_tasks=0)
		self._classify(self._message())
		self.assertEqual(self._tasks(), [])

	def test_the_ai_is_asked_for_the_tasks_with_room_to_list_them(self):
		self.assertIn("tasks", classifier.build_schema()["required"])
		self.assertIn("assignee_phone", classifier.build_system_prompt(frappe._dict()))
		doc = frappe.get_doc("Periskope Message", self._message())
		seen = {}

		response = SimpleNamespace(
			stop_reason="end_turn",
			usage=None,
			content=[SimpleNamespace(type="text", text=json.dumps(_result([])))],
		)

		def create(**request):
			seen.update(request)
			return response

		client = type("C", (), {"messages": type("M", (), {"create": staticmethod(create)})()})()
		with patch.object(classifier, "_client", return_value=client):
			classifier.call_claude(frappe._dict(ai_model="claude-haiku-4-5", ai_max_tokens=400), doc)
		self.assertGreaterEqual(seen["max_tokens"], classifier.GROUP_MAX_TOKENS)
