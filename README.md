# AIAgentPlatform — Discord ticket intake

Gates a Ticket Tool ticket behind a customisable questionnaire. The opener can't
post in their ticket until they submit the intake modal; their answers are then
rendered into a prompt for the AI agent.

## Why it works this way

Discord routes a button click **only to the application that owns the button**,
so Ticket Tool's "Create ticket" interaction never reaches this bot — there is no
event to hook, regardless of its `custom_id`. Modals compound this: they can only
open as a direct response to an interaction, so one can't be pushed at a user
unprompted.

The flow therefore reacts to the ticket channel appearing, and drives the modal
from a button this bot owns:

```
User clicks Ticket Tool's "Create ticket"
  → Ticket Tool creates #ticket-0042
  → on_guild_channel_create fires here
  → identify the opener from the channel's permission overwrites
  → deny their send_messages, remembering the previous value
  → post an embed + "Answer questions" button
  → they click → modal opens → submit
  → answers stored, prompt rendered, original permission restored
```

## Setup

1. **Discord developer portal** → your application → Bot → enable the
   **Server Members Intent**. Identifying the ticket opener reads member
   permission overwrites, which requires a populated member cache.
2. Invite the bot with **Manage Channels**, **Manage Permissions**,
   **Read Messages**, **Send Messages** and **Manage Messages** in the ticket
   categories.
3. Create `.env`:
   ```
   DISCORD_BOT_TOKEN=...
   DEV_GUILD_ID=...   # optional: instant slash-command sync while developing
   ```
4. Run it:
   ```
   python -m bot
   ```

## Configuring a server

| Command | Purpose |
| --- | --- |
| `/intake add-category` | Point the bot at the category Ticket Tool creates tickets in |
| `/intake add-question` | Add a question (max 5 — Discord's modal limit) |
| `/intake remove-question` | Delete one (autocompletes existing keys) |
| `/intake template` | Edit the agent prompt template in a modal |
| `/intake panel` | Edit the intake embed's title and description |
| `/intake preview` | Open the modal yourself and see the rendered prompt |
| `/intake status` | Review the whole configuration |
| `/intake toggle` | Turn the gate on/off |
| `/intake unlock` | Staff override to release a stuck ticket |

All commands require **Manage Server**.

### Question styles

`/intake add-question` takes a `style`, which Discord renders as a dropdown on the
command itself:

| Style | Renders as |
| --- | --- |
| **Short answer** (default) | Single-line text box |
| **Paragraph** | Multi-line text box |
| **Choice (dropdown)** | Native multi-select dropdown, 2–25 options |

Choice questions take a comma-separated `choices` list and let the user pick
**more than one** option:

```
/intake add-question
  key: ticket_type
  label: What is this ticket about?
  style: Choice (dropdown)
  choices: Scam report, Billing issue, Bug, Other
```

Selected options arrive in the prompt joined by `", "` — so picking both Scam
report and Billing issue renders `{ticket_type}` as
`Scam report, Billing issue`. Commas are the separator, so an option can't
itself contain one. Marking a choice question optional lets the user select
nothing (`min_values: 0`).

### Prompt templates

`{answers}` expands to every question and answer as labelled lines. `{question_key}`
inserts a single answer. Braces that aren't valid identifiers are left alone, so
JSON in a template survives intact.

```
A user opened a support ticket.

{answers}
```

## Layout

```
bot/
  main.py            entrypoint, intents, cog + persistent view registration
  cogs/tickets.py    channel detection, gating, modal submission
  cogs/admin.py      /intake slash commands
  services/intake.py lock/unlock, opener resolution  ← Discord-aware core
  services/prompt.py template rendering              ← pure, no discord.py
  storage/models.py  dataclasses + validation
  storage/base.py    Repository interface
  storage/memory.py  in-memory impl (dev)
  storage/schema.sql Postgres DDL
  ui/views.py        persistent button
  ui/modals.py       dynamically built intake modal
```

Business rules live in `services/` and `storage/models.py`, never in the cogs, so
the planned web dashboard can drive the same objects through the same
`Repository` interface.

## Known gaps

- **Storage is in-memory.** Config and pending intakes are lost on restart, which
  leaves already-open tickets locked. Apply `storage/schema.sql` and write a
  `PostgresRepository` against `storage/base.Repository` — that's the only
  swap needed (`main.py`).
- **Agent handoff is a TODO.** `cogs/tickets.py` logs the rendered prompt where
  the agent call belongs.
- **Five questions max.** Discord's cap. `Question.page` and the `page` columns
  exist so chained modals can be added without a migration.
- **Abandoned intakes are never reaped.** A user who opens a ticket and never
  clicks the button stays locked until staff run `/intake unlock`.
