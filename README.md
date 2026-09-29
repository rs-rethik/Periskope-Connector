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
	<a target="_blank" href="https://frappeframework.com" title="Frappe version"><img src="https://img.shields.io/badge/frappe-v15-success.svg"></a>
	<a target="_blank" href="https://docs.periskope.app" title="Periskope docs"><img src="https://img.shields.io/badge/docs-Periskope-success.svg"></a>
</div>

Sales conversations usually live on each rep's phone, so management cannot see them and handing a customer over to another rep is hard. Periskope Connector captures the WhatsApp messages on your reps' company numbers through [Periskope](https://periskope.app), saves them in Frappe, tags them with AI, and reminds reps when a customer is waiting for a reply. Reps keep using the normal WhatsApp or WhatsApp Business app on their own company numbers.

> **Status:** early development. Messages are captured, tagged and tracked. Linking them to CRM leads and deals, and acting on the tags, is the next phase (see [Roadmap](#roadmap)).

## Table of Contents
* [Features](#features)
* [How it works](#how-it-works)
* [Installation](#installation)
* [Setup](#setup)
* [Roadmap](#roadmap)
* [Development](#development)
* [License](#license)

## Features

- **Message capture:** every message on a linked number, sent or received, is saved as a **Periskope Message** with its direction, sales rep, customer number and attachment details. Duplicate events are ignored.
- **AI tagging:** each text message is tagged by Claude with business tags, plus sentiment, severity, a one-line summary and details such as a promised follow-up time or a competitor's name. About 60 default tags cover buying intent, deal progress, lead qualification, lost reasons, follow-ups and escalations. Every tag is editable and mapped to a Frappe CRM action (create a lead, update a lead or deal status, mark lost, create a task, escalate).
- **Editable AI instructions:** the instructions the AI follows can be edited in the settings page, and the **Improve Instructions with AI** button suggests a better version for you to review.
- **Token savings:** only plain text chats are analysed. Attachments, acknowledgements such as "ok" or "thanks", emoji, and one-word statements are skipped.
- **Reply reminders:** when a customer message has had no reply for a set number of working hours, the rep gets one ToDo per chat, and managers are emailed if it is still unanswered. Only your working hours and days count, so nothing fires at night.
- **Workspace and dashboard:** a Periskope workspace with shortcuts, number cards and charts for messages per day, tags, sentiment and severity, and an entry in the app list.
- **Works with other AI providers for testing:** Claude is the default. A provider with an Anthropic-compatible address, such as Z.ai GLM, can be used for testing.

## How it works

```
Rep's phone (WhatsApp)
        |  linked with a QR scan
        v
    Periskope  --- message.created webhook (HMAC signed) --->  Periskope Connector
                                                                     |
                     +-----------------------------------------------+
                     v
        Periskope Message  --->  AI tagging (Claude)  --->  tags + suggested CRM actions
                     |
                     v
        Reply reminders: ToDo for the rep, then an email to managers
```

The webhook endpoint accepts calls from anyone but only acts on a request whose `x-periskope-signature` matches your signing secret. It writes nothing until the signature is verified, and it is rate limited.

## Installation

Requirements:

- Frappe v15 and [Frappe CRM](https://github.com/frappe/crm) on the same site
- A [Periskope](https://periskope.app) account with at least one linked WhatsApp number
- An [Anthropic API key](https://console.anthropic.com) for AI tagging (optional; capture and reminders work without it)
- A site that Periskope can reach over the internet for webhooks (a public domain, or a tunnel such as ngrok while developing)

Install the app with the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app https://github.com/rs-rethik/Periskope-Connector --branch develop
bench --site $SITE_NAME install-app periskope_connector
bench --site $SITE_NAME migrate
```

Installing creates the default tags. If that step fails, the installer tells you the command to run again:

```bash
bench --site $SITE_NAME execute periskope_connector.default_tags.create_default_tags
```

## Setup

Open **Periskope Settings** in Desk. The page has four tabs, and the **Actions** menu has the buttons for each step.

1. **Connection**
   1. Paste the Periskope **API Key** (Periskope console > Settings > Integrations > API) and use **Actions > Test Connection**. It shows how many phones were found and fills in the **Linked WhatsApp Phones** table.
   2. Choose the **Sales Rep** for each phone. The rep is kept when you sync again.
   3. Copy the **Webhook URL** into Periskope (Settings > Webhooks) for the `message.created` event (the only one saved for now; other events are just logged), then paste Periskope's **Webhook Signing Secret** here.
2. **AI Tagging:** enter the AI API key, use **Actions > Test AI Connection**, and switch on **Tag New Messages with AI**. Add a short **About Your Business** note to help the AI, and edit the tags in **Periskope Message Tag** whenever you like.
3. **Reply Reminders:** set your working hours and days, how long a customer may wait, and the **Manager Emails**, then switch reminders on. **Actions > Check Reply Reminders Now** runs the check immediately.
4. **Advanced:** the Periskope API address, which rarely needs changing.

> **Privacy:** the text of tagged messages is sent to your AI provider. Make sure that fits your data policy before switching AI tagging on, and enter API keys on the settings page yourself.

## Roadmap

- [x] Periskope connection, webhook receiver and message capture
- [x] AI tagging with editable tags mapped to CRM actions
- [x] Reply reminders with working hours and manager escalation
- [ ] Link messages to CRM leads and deals, and create leads from new numbers
- [ ] Act on the tags: update lead and deal status, mark lost, create tasks
- [ ] Alert management about serious issues by email, Slack or WhatsApp
- [ ] Show conversations in the CRM lead and deal pages, with reports and handover

## Development

Run the tests on a development site. They roll back everything they write and never touch your saved API keys, but they do run against the site's database:

```bash
bench --site $SITE_NAME run-tests --app periskope_connector
```

The repo uses `pre-commit` (ruff, eslint, prettier). Enable it once with `pre-commit install` in this folder.

Useful commands:

```bash
# tag messages that are still waiting for the AI
bench --site $SITE_NAME execute periskope_connector.ai.classifier.classify_pending

# run the reply reminder check by hand
bench --site $SITE_NAME execute periskope_connector.reminders.run
```

## License

This repository has been released under the [MIT License](license.txt).
