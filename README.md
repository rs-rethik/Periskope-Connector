<div align="center">
	<h1>
		<br>
		<a href="https://github.com/rs-rethik/Periskope-Connector">
			<img src=".github/logo.svg" height="80" alt="Periskope Connector Logo">
		</a>
		<br>
		Periskope Connector
	</h1>
	<h3>
		see every WhatsApp conversation your sales team has, inside Frappe CRM
	</h3>
	<h5>
		no WhatsApp Business API needed - reps keep using the WhatsApp they already use
	</h5>
</div>

<div align="center">
	<a target="_blank" href="#license" title="License: MIT"><img src="https://img.shields.io/badge/License-MIT-success.svg"></a>
	<a target="_blank" href="https://www.python.org/downloads/" title="Python version"><img src="https://img.shields.io/badge/python-%3E=_3.10-success.svg"></a>
	<a target="_blank" href="https://frappeframework.com" title="Frappe version"><img src="https://img.shields.io/badge/frappe-v15%20%7C%20v16-success.svg"></a>
	<a target="_blank" href="https://docs.periskope.app" title="Periskope docs"><img src="https://img.shields.io/badge/docs-Periskope-success.svg"></a>
</div>

Sales conversations usually live on each rep's phone, so management cannot see them and handing a customer over to another rep is hard. Periskope Connector captures the WhatsApp messages on your reps' company numbers through [Periskope](https://periskope.app), saves them in Frappe, classifies them with AI, links them to CRM leads and deals, escalates unanswered customers through an SLA ladder, and alerts management to serious issues. Reps keep using the normal WhatsApp or WhatsApp Business app on their own company numbers. Nothing in the app ever sends a WhatsApp message.

> **Status:** feature-complete for the spec (capture, AI tagging, CRM linking, contact queue, SLA escalation, keyword and high-severity alerts, health monitoring, reports).

## Table of Contents
* [Features](#features)
* [How it works](#how-it-works)
* [Installation](#installation)
* [Setup](#setup)
* [Privacy](#privacy)
* [Roadmap](#roadmap)
* [Development](#development)
* [License](#license)

## Features

- **Message capture:** every message on a linked number, sent or received, is saved as a **Periskope Message** with its direction, sales rep, customer number and attachment details. Duplicate events are ignored; a background sweep recovers deliveries whose job was lost; a daily sync-health check emails you when a normally busy number goes silent.
- **AI classification:** each text message is put in one of six categories by Claude (`quality_complaint`, `delivery_delay`, `payment_issue`, `sample_request`, `order_or_enquiry`, `general`), with severity, sentiment, a one-line summary and details such as a promised follow-up time, the customer's name or a competitor's name. Only the customer's text messages are analysed (20 per 5-minute run, chat history as context); acknowledgements, emoji and one-worders are skipped. A sales manager can record the correct category when the AI was wrong — the latest corrections are fed back into the prompt, and the **Periskope Tagging Review** report shows the correction rate. Every call's tokens are logged (**Periskope AI Usage**), an optional monthly budget pauses tagging when reached, and the instructions are editable (with an **Improve Instructions with AI** button).
- **CRM linking:** 1-to-1 chats match the customer's number against CRM Lead and Deal (indexed normalised fields, E.164 first so foreign buyers cannot collide; open deals before closed ones). Customer groups match by group name, sender number, the customer name the AI finds, or a lead/deal set by hand on the **Periskope Group**. Classified messages appear on the lead/deal timeline as comments (one per message, burst-merged, or off — your choice; Desk's connections sidebar links the history either way).
- **Lead capture:** unknown 1-to-1 numbers land in the **Periskope Contact** queue with one-click *Create Lead*, *Link to existing* and *Ignore* (personal/vendor/spam/internal) actions, a daily digest to the sales head, dedupe against existing leads, deals and contacts, and an ignore list (optionally fed by ERPNext Supplier/Employee numbers). Automatic lead creation for new enquiries is a switch (off by default).
- **SLA escalation:** unanswered customer messages escalate per chat — L1 a bell for the rep (1 working hour), L2 a ToDo plus a manager email (3), L3 an email to the MD list (8), and a High-severity message unanswered for 30 minutes jumps straight to the top. Working hours, working days and a Holiday List (Diwali) are respected; the reply closes everything and records the response time; reports show first-response time per rep and every open escalation.
- **Alerts:** high-severity messages email the managers the moment they are classified, and keyword rules (editable, whole-word, Tamil/Tanglish friendly, per-rule recipients, with a dedupe window so one argument is one alert) catch what the AI files too politely. Every alert is logged for audit.
- **Visibility and control:** a Periskope workspace with shortcuts, dashboards and reports (Tagging Review, Response Time, Open Escalations, Lead Source, AI Usage, Sync Health, Unmatched Numbers); only Sales Manager / System Manager / WhatsApp Manager can read the messages (reps can optionally see their own numbers); a Health tab with live figures; retention settings that clear raw payloads after 90 days by default.

## How it works

```
Rep's phone (WhatsApp)
        |  linked with a QR scan
        v
    Periskope  --- message.created webhook (HMAC signed) --->  Periskope Connector
                                                                     |
                     +-----------------------------------------------+
                     v
        Periskope Message  --->  AI classification (Claude)  --->  category, severity, summary
                     |                |                                     |
                     |                +-> keyword alerts, high-severity alert
                     +--> linked to a CRM Lead or Deal (new enquiry: lead + timeline comment)
                     |
                     +--> unknown number -> Periskope Contact queue (daily digest)
                     |
                     v
        SLA ladder: rep bell -> ToDo + managers -> MD  (working hours, holidays, re-arm)
```

The webhook endpoint accepts calls from anyone but only acts on a request whose `x-periskope-signature` matches your signing secret. It writes nothing until the signature is verified, it is rate limited, and heavy work happens in a background job so Periskope gets its 200 immediately.

## Installation

Requirements:

- Frappe v15 or v16 with [Frappe CRM](https://github.com/frappe/crm) on the site (ERPNext optional: it only adds the Sales Person link and Supplier/Employee ignore lookups)
- A [Periskope](https://periskope.app) account with at least one linked WhatsApp number
- An [Anthropic API key](https://console.anthropic.com) for AI tagging (optional; capture, linking and escalation work without it)
- A site that Periskope can reach over the internet for webhooks

Install the app with the [bench](https://github.com/frappe/bench) CLI — everything else is created for you (roles, custom fields, lead source, starter keyword rules):

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/rs-rethik/Periskope-Connector --branch develop
bench --site $SITE_NAME install-app periskope_connector
bench --site $SITE_NAME migrate
```

## Setup

Open **Periskope Settings**. The tabs walk you through it: **Connection** (API key, webhook URL + signing secret, phone-to-rep assignment), **AI Tagging** (provider, key, model, instructions, business context), **Lead Capture** (auto-create switch, ignore list, digest recipients), **Reply Reminders** (working hours, holiday list, SLA levels, manager and MD emails, acknowledgement words), **Keyword Alerts** (rules and dedupe window — a rule sends nothing until it has recipients), **Data & Cost** (token budget, max tokens, retention, media download), **CRM Visibility** (comment mode, rep self-visibility) and **Health** (live figures).

## Privacy

Message text is sent to the AI provider you configure — Anthropic by default; the GLM/Z.ai and Jarvis options send it to those providers instead, so choose them only if that fits your policy. Only company numbers linked in Periskope are captured; personal or mixed-use numbers can be excluded on the ignore list, and staff should be told that business WhatsApp numbers are monitored for record-keeping. Raw webhook payloads are cleared after 90 days by default and message text can be given its own retention. Under India's DPDP Act 2023 the operator remains the data fiduciary for this processing.

## Roadmap

- [x] Periskope connection, webhook receiver and message capture
- [x] AI classification into six categories, with feedback loop and spend controls
- [x] Link messages to CRM leads and deals; lead capture via the contact queue
- [x] SLA escalation with working hours and holidays; keyword and high-severity alerts
- [x] Health monitoring, reports, retention
- [ ] Edit/delete webhook events and history back-fill — waiting on real Periskope payload samples and API docs
- [ ] Callyzer SIM call logging (a later project, reusing the same linking pattern)

## Development

Run the tests on a development site. They roll back everything they write and never touch your saved API keys, but they do run against the site's database:

```bash
bench --site $SITE_NAME run-tests --app periskope_connector
```

The repo uses `pre-commit` (ruff, eslint, prettier). Enable it once with `pre-commit install` in this folder.

Useful commands:

```bash
# classify the messages that are still waiting for the AI (the scheduler does this every 5 minutes)
bench --site $SITE_NAME execute periskope_connector.ai.classifier.classify_pending

# run the SLA / reminder check by hand
bench --site $SITE_NAME execute periskope_connector.reminders.run

# re-link messages that could not be matched, and back-fill the contact queue
bench --site $SITE_NAME execute periskope_connector.linking.link_unlinked
bench --site $SITE_NAME execute periskope_connector.contacts.backfill_contacts
```

## License

This repository has been released under the [MIT License](license.txt).
