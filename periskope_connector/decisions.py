"""Carry out what the AI decided about a message. The AI is the controller: there are no rules on tags
or keywords. Every step is guarded, so one failing never stops the others or undoes the analysis.

- create_lead: a new business enquiry from a number the CRM does not know becomes a CRM Lead
  (leads.py keeps its safety checks: 1-to-1, unknown number, not an ignored or known contact).
- escalate: the managers or the MD get an email and a bell at once (alerts.py).
- tasks: each job handed out becomes a CRM Task on the customer's record (chat_tasks.py).
- on_timeline: the message shows on the lead or deal timeline (timeline_comments.py, when it is
  linked; linking.link_message adds it).
- The CRM record's summary, notes, follow-up task and status are kept current per chat
  (ai/analyse.py), and the auto reply answers the customer when no rep does (autoreply.py).
"""

from periskope_connector import alerts, chat_tasks, leads


def act(message: str, result: dict):
	if result.get("create_lead"):
		leads.create_lead_for_message(message)
	alerts.escalate(message)
	chat_tasks.from_message(message, result.get("tasks"))
