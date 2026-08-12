# ReportPilot client agent (Ingestion Mode 1: "client push")

A small Windows program that watches a Gmail/Outlook inbox or a Slack
channel for new CSV/Excel exports and submits them to your ReportPilot
report backend automatically, on a schedule you control via Windows Task
Scheduler. Nothing runs continuously — it wakes up, checks for anything
new, submits it if found, and exits.

Use this when your IT is willing to run something locally on a schedule,
but doesn't want to give the report vendor a direct connection to your
data warehouse or hand over inbox credentials for them to hold. Your
credentials never leave this machine except to log into your own mailbox
or Slack — the report backend only ever receives the files themselves.

## How it works

1. `reportpilot-agent.exe setup` — run once. Saves your mailbox or Slack
   credentials, encrypted, to this machine.
2. `reportpilot-agent.exe run` — run on a schedule (Task Scheduler). Checks
   for new matching attachments, submits them, and exits.

Attachments are matched to a report section by filename — a name
containing `analytics`/`sessions`/`ga4` goes to the Web Analytics section,
`seo`/`audit`/`crawl` to SEO, `sales`/`pipeline`/`deals`/`crm` to Sales.
Anything else is skipped and printed to the console, never silently
dropped.

## Credential storage

Credentials are encrypted with Windows' own DPAPI (`CryptProtectData`),
the same mechanism Windows uses to protect saved Wi-Fi passwords and
Credential Manager entries. The encrypted file
(`%LOCALAPPDATA%\ReportPilotAgent\config.bin`) can only be decrypted by
the same Windows user account, on the same machine, that ran `setup`. If
this agent is deployed to a new machine, or set up to run as a different
Windows account (see the Task Scheduler note below), run `setup` again
under that account.

## Setup

### 1. Get credentials for your source

**Gmail** — turn on 2-Step Verification, then create an
[App Password](https://myaccount.google.com/apppasswords) (not your normal
Google password).

**Outlook / Microsoft 365** — a personal outlook.com account, or a work
account whose admin still allows IMAP basic auth, can use an app password
the same way. Most modern Microsoft 365 business tenants have disabled
this — if `setup`/`run` reports an authentication failure and you know
the password is correct, your tenant likely needs Microsoft Graph API
access instead, which this agent does not currently support; ask your
consultant about Mode 2 (hosted) or Mode 3 (warehouse) instead.

**Slack** — from [api.slack.com/apps](https://api.slack.com/apps), create
an app, add the `files:read` and `channels:history` (or `groups:history`
for a private channel) Bot Token Scopes under **OAuth & Permissions**,
install the app to your workspace, and copy the **Bot User OAuth Token**
(`xoxb-...`). Invite the bot to the channel where the CSV/Excel files get
posted.

### 2. Run setup

Open Command Prompt where `reportpilot-agent.exe` is saved and run one of:

```
reportpilot-agent.exe setup --source gmail --username you@yourcompany.com --password your-app-password --api-base-url https://your-reportpilot-host.example.com --api-key your-api-key

reportpilot-agent.exe setup --source outlook --username you@yourcompany.com --password your-app-password --api-base-url https://your-reportpilot-host.example.com --api-key your-api-key

reportpilot-agent.exe setup --source slack --bot-token xoxb-... --channel-id C0123456789 --api-base-url https://your-reportpilot-host.example.com --api-key your-api-key
```

`--api-key` is required: your consultant creates it for your workspace at
**POST /api/auth/tokens** while logged into the ReportPilot web app, and
sends it to you to paste in here. `--client-name`/`--agency-name`/
`--primary-color`/`--accent-color` are optional branding for the generated
report; ask your consultant for the right values.

Run `reportpilot-agent.exe setup --help` for every option, including
`--mailbox`, `--search` (IMAP search query, default `UNSEEN`), and
`--no-mark-as-read` (by default, fetched emails are marked read so the
next run doesn't resubmit them).

### 3. Test it without sending anything

```
reportpilot-agent.exe run --dry-run
```

This connects to your inbox/Slack for real and reports what it *would*
submit, without actually sending anything to the report backend. Use this
to confirm credentials and filename matching are correct before scheduling
anything.

### 4. Schedule it with Task Scheduler

1. Open **Task Scheduler** → **Create Task** (not "Basic Task", so you get
   the full options below).
2. **General** tab: give it a name (e.g. "ReportPilot weekly report").
   Under **Security options**, choose "Run whether user is logged on or
   not" if you want it to fire even when nobody's signed in, and "Run with
   highest privileges" is not required.
3. **Triggers** tab → **New**: Weekly (or Monthly), pick the day/time your
   CSV export usually arrives, with some buffer (e.g. a few hours after
   it's expected).
4. **Actions** tab → **New** → **Start a program**: browse to
   `reportpilot-agent.exe`, and in **Add arguments** put `run`.
5. Save. You'll be prompted for the Windows account's password if you
   chose "run whether logged on or not" — **use the same Windows account
   you ran `setup` under**, or `run` won't be able to decrypt the saved
   config (see Credential storage above), and `run`'s log will say so
   clearly rather than failing silently.

### 5. Check that it's working

Every run appends one line to `%LOCALAPPDATA%\ReportPilotAgent\agent.log`
— when it ran, what happened, and any error. Task Scheduler's own task
history also shows the exit code: `0` for success (including "nothing new
to submit"), `1` for a real failure worth investigating.

## What this does *not* do

- It does not generate the report itself — it fetches files and hands them
  to your ReportPilot backend, which does the actual computation and
  narrative generation. This machine just needs network access to that
  backend's URL.
- It has no server-side idempotency ledger the way a hosted schedule does
  — "don't resubmit the same email" is handled by marking fetched emails
  as read (default on), not by tracking which reports were already
  generated. If you disable `--no-mark-as-read`, re-running without new
  mail will resubmit the same attachments and generate a duplicate report.
- It's Windows-only, by design (it uses Windows' own credential
  encryption). There's no macOS/Linux build.

## Rebuilding the .exe

From `client_agent/`, with the backend's virtualenv active:

```
pip install -r requirements.txt
pyinstaller --onefile --name reportpilot-agent --distpath dist --workpath build --specpath . agent.py
```

The result is `dist/reportpilot-agent.exe` — a single file, nothing else
to distribute alongside it.
