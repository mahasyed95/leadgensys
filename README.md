# Outreach Sys — Runbook

Operating spec is [CLAUDE.md](CLAUDE.md). This file is how you actually run it.

**Nothing sends unless you pick an `Approve Draft` option in that row's `approve` column.**
Email is automated after that; **LinkedIn is always sent by hand.**

---

## One-time setup

```bash
# 1. Dependencies (isolated venv — system Python is untouched)
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# 2. Config
cp .env.example .env        # fill in APOLLO_API_KEY, SENDER_NAME, etc.

# 3. Google OAuth — Gmail and Sheets need this, not an API key
#    console.cloud.google.com -> new project
#    -> enable "Gmail API" + "Google Sheets API"
#    -> OAuth consent screen: INTERNAL (not External — on External the refresh
#       token expires every 7 days and cron dies silently)
#    -> Credentials -> OAuth client ID -> Desktop app -> download
#    -> save as credentials.json in this folder
.venv/bin/python scripts/google_auth.py     # opens a browser once

# 4. Create a Google Sheet, put its URL (or bare id) in GOOGLE_SHEET_ID in .env
.venv/bin/python scripts/sheets.py --init     # creates the three tabs
.venv/bin/python scripts/sheets.py --format   # dropdown + colours on the approval tab

# 5. Confirm
.venv/bin/python scripts/config.py
```

---

## Folder map

```
Assets/              portfolio + case studies (Olivea, Helafit, Perfect Body, Insynergy, CMD)
Leads/
  Inbox/             raw Apollo pulls land here
  Batches/           qualified leads, ready to draft
  Rejected/          rejects with reasons (audit trail + filter tuning)
  master-list.csv    every lead ever contacted — the dedup ledger
  suppression.csv    opt-outs — checked again at send time
Sequences/
  Templates/         sequence skeletons per ICP + example-drafts.json
  Drafts/            drafted sequences as JSON, before they go to the Sheet
scripts/
  apollo_pull.py     Apollo API -> CSV
  process_batch.py   dedup + qualify + diversify
  push_to_sheet.py   drafts -> Sheet Outbox, 3 variants per lead
  send_approved.py   sends the variant you picked, via Gmail  (cron)
  sheets.py          Sheet schema + read/write
  google_auth.py     OAuth for Gmail + Sheets
  record_sent.py     local ledger + opt-out suppression
  config.py          reads .env
```

The Sheet has three tabs: **Leads** (CRM), **Outbox** (where you approve), **Suppression** (opt-outs).

---

## The loop

### 1. Source — export from the Apollo web UI
Apollo's **search and match APIs require a paid plan**; on the Free plan they return 403.
So sourcing is a manual export, and everything after it is automated.

In the Apollo UI, apply the filters from [CLAUDE.md](CLAUDE.md) §3 (ecom) or §4 (agency),
export to CSV, and save it into `Leads/Inbox/`.

**Free-tier reality:** ~10 export credits/month, consumed per contact, 25 records per
export. That is roughly 10 leads a month — enough to prove the system works, not enough
to fill a pipeline. Upgrading Apollo (or switching source) is what unblocks volume;
`apollo_pull.py` then automates this step with no code changes.

Pull 3-4× your target — qualification is strict. And vary niche, geography, and
company-size band between batches; the diversification cap can trim a skewed batch but
can't fix a narrow export.

> `scripts/apollo_pull.py` automates this step and is written and working, but stays
> unusable until the Apollo plan includes API access. If you ever upgrade, it needs no
> changes — it writes the same CSV shape this manual export produces.

**Alternative source — Firecrawl (no Apollo credits):**
```bash
.venv/bin/python scripts/firecrawl_source.py --icp ecom --limit 20 --require-ecom-tech
```
Searches the public web, scrapes each candidate's homepage, and detects the **live**
tech stack (Shopify, Klaviyo, Recharge, Gorgias, Postscript…) — more current than any
database. Writes the same Apollo-shaped CSV into `Leads/Inbox/`.

Its limit is contacts: sites publish role inboxes (`hello@`, `info@`), not the founder's
address. Those rows carry status `scraped` and **`process_batch.py` rejects them by
default**. Two ways to use them:

- **Best use** — treat the output as a pre-qualified target list, then spend your scarce
  Apollo credits looking up named decision-makers at those specific domains.
- **Or** accept role inboxes for a batch with `--allow-role-emails`. At a ten-person
  brand the founder often reads `hello@`; at a hundred-person one it's a support queue.
  Drafts for these must not open with a first name — there isn't one.

Needs `FIRECRAWL_API_KEY` in `.env` (free tier at firecrawl.dev).

### 2. Process
```bash
# one file, several files, or the whole Inbox directory
.venv/bin/python scripts/process_batch.py Leads/Inbox --icp ecom --limit 40
```
Pass a directory and every CSV in it is **merged before dedup runs**. That matters on
Apollo's free tier, where a 25-record export cap means one batch arrives as several
files — processed separately, the same company in two exports would get two sequences.
Enforces dedup (email + domain, against the ledger and suppression), one contact per company, ICP fit, employee band, verified email, in-scope country, and the 25% per-industry cap.

Skim `Leads/Rejected/` now and then — if good leads are being cut, tell me and I'll tune the filters.

### 3. Draft
Ask me to draft the batch. I research each lead and write **three different angles** on
the same signal, then save JSON to `Sequences/Drafts/`. Leads with no genuine signal get
flagged, not given a generic opener.

Format: see [Sequences/Templates/example-drafts.json](Sequences/Templates/example-drafts.json).

### 4. Push to the Sheet
```bash
.venv/bin/python scripts/push_to_sheet.py Sequences/Drafts/<file>.json --dry-run
.venv/bin/python scripts/push_to_sheet.py Sequences/Drafts/<file>.json
```
One row per lead, three drafts side by side, `approve` left blank.

It refuses to push a draft with no recorded signal, an empty subject/body, fewer than
three variants, or two variants that are the same message twice.

### 5. Approve — the only manual gate
Open the Outbox tab. Each row is one lead with three drafts across it:

```
email              signal        subject_1  body_1  subject_2  body_2  subject_3  body_3  approve
sarah@olivea.com   CTA buried…   your wel…  …       the reor…  …       same cop…  …         2
```

Read the three. Edit any cell directly if you want. Then pick from the **`approve`**
dropdown — that's the whole gate.

| `approve` | What happens |
|---|---|
| blank | nothing, ever |
| `Approve Draft 1` / `2` / `3` | that draft emails out on the next run |
| `Reject` | row is cancelled, never sends, lead marked Not Interested |
| `Redraft` | flagged for a rewrite — `list_redrafts.py` lists them. Never sends |
| anything else | treated as blank — nothing sends |

Rows go **green** the moment an Approve option is chosen — green means armed. Amber is
`Redraft`, mauve is `Reject`. `status` is just the record afterwards (`SENT`,
`CANCELLED`, `FAILED`); it never causes a send. After delivery, `sent_variant` records
which of the three went out.

If you approve a variant that's empty, the sender refuses and tells you rather than
mailing a blank.

**LinkedIn is the primary channel and it is yours to send.** Each row carries
`li_note` (connection request, ≤300 chars) and `li_dm` (follow-up), ready to copy.
Track your own progress in `li_status` — nothing here writes that column, because
nothing here touches LinkedIn. Rows marked `email-only` have no profile to reach.

### 6. Send
```bash
.venv/bin/python scripts/send_approved.py --dry-run   # always first
.venv/bin/python scripts/send_approved.py
```

Before each individual send it re-checks the suppression list and whether the lead has replied. Status is written back to the Sheet immediately after each send, so an interrupted run can't double-send.

`send_approved.py --watch` keeps running and sends each row as you approve it. The daily
cap (`GMAIL_DAILY_CAP`, default 40) applies across every run in a day, so a second run
can't blow past it.

**Before it will send anything, `SENDER_ADDRESS` must be a deliverable postal address.**
Not merely non-empty — CAN-SPAM requires an address a letter could actually arrive at,
so the sender checks for a recognisable postcode and refuses to start without one.
`30 Riverhead Close, Wales` is rejected: no town, no postcode. If your address genuinely
has no postcode, set `SENDER_ADDRESS_VERIFIED=1`.

### Scheduling on this Mac — read this before arming anything

**Neither cron nor launchd can run these scripts as things stand.** The project lives
under `~/Desktop`, which macOS protects under TCC, and no scheduler has Full Disk Access
by default. Both fail identically at exec:

```
/bin/bash: .../scripts/daily_source.sh: Operation not permitted     # exit 126
```

Two fixes, pick one:
- **Grant Full Disk Access to `/bin/bash`** — System Settings → Privacy & Security →
  Full Disk Access → `+` → ⌘⇧G → `/bin/bash`. Project stays where it is.
- **Move the project out of `~/Desktop`** — removes the whole class of problem.

Use **launchd, not cron**. cron silently skips any run scheduled while the laptop is
asleep and never catches up; launchd's `StartCalendarInterval` runs a missed job on wake.
The daily agent is `~/Library/LaunchAgents/com.mailitbetter.dailysource.plist`, logging
to `~/Library/Logs/mailitbetter-daily.log` — **deliberately outside the project**, because
a log inside a TCC-blocked folder is unwritable in exactly the failure case you need it for.

Until permission is granted, run it by hand from Terminal (which does have access):
```bash
bash scripts/daily_source.sh
```

### 7. Opt-outs — same day
```bash
.venv/bin/python scripts/record_sent.py --suppress someone@example.com --reason "unsubscribed"
```
Also add them to the Sheet's Suppression tab. Either location blocks the send; both is belt and braces.

---

## Tests

```bash
.venv/bin/python scripts/test_pipeline.py       # 64 — dedup, domains, ICP, revenue band,
                                                #      MX, diversification axis, columns
.venv/bin/python scripts/test_send_safety.py    # 78 — approval gate, replies, suppression,
                                                #      compliance footer, postal-address
                                                #      validity, sent-ledger, append
                                                #      idempotency, push-time dedup
```

Run both after any change. A dedup regression double-contacts companies; a gating regression sends unapproved mail.

---

## Rules that don't bend

- **Only you touch `approve`.** I never write that column, and I never widen the sender's gate.
- LinkedIn and Instagram/FB are hand-sent. No automation, ever.
- Opt-outs are honored the day they arrive. US/CA/EU/UK/AU are all in scope, so CASL and GDPR are the floor.
- Plain text, one link max, zero links in a cold first touch.
- Never fabricate a metric or a personalization signal.

---

## Not built yet

| Integration | Status |
|---|---|
| Apollo API sourcing | Code complete, blocked on Apollo's Free plan (403 on search + match). Manual CSV export used instead. |
| Email verification (NeverBounce/ZeroBounce) | Not built. Currently relies on Apollo's `Email Status`. |
| Reply classification | Replies stop a sequence automatically, but sorting them into interested / not / opt-out is manual. |
| LinkedIn / IG drafting into the Sheet | Drafted in chat today, not tracked in the Outbox. |

---

## Verification status

| Path | State |
|---|---|
| Google OAuth | **Verified** — authorized as maha@mailitbetterco.com, refresh token present |
| Sheets read/write/init | **Verified live** — tabs created, drafts pushed, rows cleared |
| Approval gate | **Verified live** — with unapproved rows present, the sender reported nothing due |
| Apollo API | Key valid (`auth/health` 200); search/match blocked by Free plan |
| **Gmail send** | **Not yet verified.** Auth works and the profile call succeeds, but no message has been sent. |

Before the first prospect, do a live test to your own address: push a draft addressed to
yourself, approve that row, then `send_approved.py --limit 1`. Confirm it arrives with
the right footer and threads correctly.
