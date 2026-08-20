# Where things stand — 2026-08-20

## Full audit, 2026-08-20 — 9 issues found and fixed

Tests: **64 + 78 green** (was 64 + 53). Every fix below has regression cover.

### Compliance (would have sent illegal mail)

1. **The compliance footer could vanish entirely.** `compliance_footer()` returned the
   body untouched whenever it contained the substring "unsubscribe" or "opt out" —
   meaning to avoid a doubled opt-out line, but also stripping the sender name, the
   company, and the **postal address**. For an email-marketing agency that misfire was
   close to guaranteed: "your unsubscribe rate is climbing" is ordinary copy here, and
   every such draft would have shipped with no address. CLAUDE.md Section 13 calls this
   illegal in the US, not untidy. The identification block is now unconditional; only
   the opt-out *sentence* is conditional, and only on a genuine opt-out *instruction*
   (`OPTOUT_INSTRUCTION`), not a passing mention of the word.

2. **The address guard checked presence, not deliverability.** `if not SENDER_ADDRESS`
   passes `30 Riverhead Close, Wales` — a street and a country, no town, no postcode,
   undeliverable. Mail under it is as non-compliant as mail with no address at all.
   New `address_looks_deliverable()` requires a recognisable UK/US/CA/AU postcode and
   refuses the run otherwise. Override with `SENDER_ADDRESS_VERIFIED=1`.

### Data integrity (would have double-contacted people)

3. **`sheets.append` was retried but is not idempotent.** `_run` retries transient
   failures and its docstring claimed every call was idempotent — append is the one
   that isn't. A timeout raised *after* the server committed would replay the write and
   give one lead two Outbox rows; two identical rows both look approvable, so the lead
   gets mailed twice. append now drives its own retry (`_run(..., attempts=1)`) around a
   re-read, so a half-completed attempt is skipped by the next one.

4. **The sent-ledger backstop was silently off for some rows.** `mark_sent_key("")`
   wrote a blank line, which `load_sent_keys()` then discarded — so a row with no
   `row_key` had no protection at all. If the Sheets write failed after delivery, that
   row stayed armed and went out again. New `sent_key(row)` falls back to the email
   address and warns when a row has no identity at all.

5. **Domain-level dedup was missing at push time.** CLAUDE.md Section 8 calls it a
   standing rule — one person per company at a time — but `push_to_sheet` matched on
   `row_key` (the email) only, so `alice@acme.com` and `bob@acme.com` both got rows.
   The Apollo path happened to catch it via `sourced-ledger.csv`; a hand-written or
   re-drafted JSON went straight past. Now dedupes on registrable domain, with freemail
   excluded (two prospects on gmail.com are not one company).

6. **`--watch` trusted a header cache for hours.** `_header_cache` is filled once per
   process and the watcher runs all day; a column dragged to a new position mid-run
   would have every write computed from the stale order — stamping SENT over a message
   body. New `sheets.invalidate_header_cache()`, called each cycle.

### Spec violations / robustness

7. **`push_to_sheet` wrote `li_status`** — seeding "To send" directly beneath a comment
   claiming nothing in the repo ever writes it, and against CLAUDE.md Sections 12 and
   13, which reserve that column for Maha. Seeding a value is harmless in itself, but an
   invariant the code quietly breaks is not an invariant, and this one guards the channel
   with the ban risk. Now genuinely never written. The dropdown still offers "To send";
   blank reads the same way. *If you want the seed back, that is a one-line CLAUDE.md
   change for you to approve — not mine to make.*

8. **Unattended auth could hang forever.** A revoked or expired refresh token fell
   through to `run_local_server()`, which opens a browser and blocks until someone
   clicks. Under launchd or `--watch` there is no browser and no one to click: the daily
   run hangs holding its lock, or the watcher looks alive while sending nothing. Now
   refuses with instructions unless a TTY is present (`OUTREACH_ALLOW_BROWSER_AUTH=1`
   forces it).

9. **`icp_evidence` crashed on a non-numeric revenue.** `int(lead['revenue'])` on a
   hand-written `"12M"` rejected an otherwise valid batch over a cosmetic column.

Also: README test counts were stale (22/24 vs 64/78) and it still told you to arm a cron
that cannot run on this machine. Both corrected.

## Still blocked on Maha — cannot be fixed from here

1. **Scheduling.** Proven, not assumed: `launchctl kickstart` returns **exit 126**,
   `Operation not permitted`. cron and launchd fail identically because the project sits
   under `~/Desktop` (TCC-protected) and no scheduler has Full Disk Access. Changing the
   time cannot help (denied at exec); retrying cannot help (deterministic); GitHub/cloud
   cannot help (`token.json`, `credentials.json` and the Apollo key are local and
   gitignored and must stay that way). Either grant Full Disk Access to `/bin/bash`, or
   move the project off `~/Desktop`.
   **Fallback that works today:** `bash scripts/daily_source.sh` from Terminal.
2. **`SENDER_ADDRESS`** needs a town and postcode. Now enforced — the sender refuses to
   start rather than sending non-compliant mail.
3. **The dead cron line** needs `crontab -e` by hand; `/var/at` is not writable from the
   agent sandbox.

## Open question

CLAUDE.md Section 10 makes variant 3 proof-led, but Section 5 bars case studies from the
agency pitch, so an agency batch has no proof to lead with. Variant 3 is currently a
capacity/process angle — no client names, no metrics. Say if you want it differently.

## Live right now

- **19 Outbox rows**, every `approve` blank, nothing sent. 15 ecom + 4 agency.
- 2026-08-17 agency batch: 15 sourced → 12 qualified → 4 drafted, 5 dropped, 3 flagged.

---

# Where things stand — 2026-08-18

## The daily trigger: still blocked by macOS, and it is not a scheduler problem

Proven, not assumed. `launchctl kickstart` of the agent returns **exit 126**:

    /bin/bash: .../scripts/daily_source.sh: Operation not permitted

cron and launchd fail identically. The project lives under `~/Desktop`, a TCC-protected
folder, and neither scheduler has Full Disk Access. Changing the schedule time does not
help (it is denied at exec), and retrying does not help (it fails deterministically).
Pushing to GitHub does not help either: the blocker is that `token.json`, `credentials.json`
and the Apollo key are local and gitignored and must stay that way, so a cloud runner
cannot read the Sheet or send from the warmed mailbox without exporting them.

**Two real fixes, both Maha's call:**
1. Full Disk Access for `/bin/bash` (System Settings → Privacy & Security). ~30s, project
   stays put. Cost: `/bin/bash` gets broad disk access system-wide.
2. Move the project out of `~/Desktop` (e.g. `~/OutreachSys`). No GUI step, removes the
   whole TCC class of problem. Cost: relocates the folder named DO NOT TOUCH.

**Working fallback until then:** Terminal *does* have disk access, so
`bash scripts/daily_source.sh` run by hand does the full pull → qualify → draft → push.
That is how 2026-08-17 was completed.

## Fixed 2026-08-17/18

- **cron → launchd** (`~/Library/LaunchAgents/com.mailitbetter.dailysource.plist`).
  cron silently skipped every morning the laptop was asleep — 3 of the first 5 days —
  and never catches up. launchd runs a missed `StartCalendarInterval` job on wake.
- **Scheduler log moved outside the project** → `~/Library/Logs/mailitbetter-daily.log`.
  A log inside a TCC-blocked folder is unwritable in exactly the failure case you need
  it for; this is why the first five failures were completely invisible.
- **The log used to lie.** `daily_source.sh` printed "Done. Drafts are in the Outbox"
  whenever `claude -p` exited 0 — but the drafting agent exits 0 when it *correctly
  declines to draft*. It now counts draft files before/after and exits 2 with
  `INCOMPLETE`. The false line of 2026-08-17 is annotated in `Leads/daily.log`, not
  deleted.
- **Drafting had no research tools.** The unattended run had no WebFetch/WebSearch, so
  it could not open a single prospect site and refused to draft — correct per §9, but a
  wasted run. Now passes `--allowedTools "...,WebFetch,WebSearch,..."`. This is a tool
  allowlist only; it does not widen the approval gate.
- **Run-once-a-day guard** (`Leads/.daily.lock` via atomic mkdir, stale after 6h, plus
  `Leads/.last-daily-run`; `FORCE_DAILY=1` overrides). Two schedulers pointing at the
  script would otherwise buy 30 Apollo credits for 15 leads.
- **Diversification cap would have gutted agency batches.** The 25% cap counted on
  *industry*, but for ICP #2 "marketing & advertising" is the ICP, not a niche. At 20+
  qualified leads it would have rejected ~3/4 of a good agency batch. The axis is now
  per-ICP — ecom on industry, agency on size band — keyed off each lead's own `icp`
  (the `--icp` flag is optional, so mixed batches must count both ways). Missing
  headcount returns `unknown`, not `1-9`.

## Live right now

- **19 Outbox rows**, every `approve` blank, nothing sent. 15 ecom (2026-08-12) +
  4 agency (2026-08-17).
- **2026-08-17 agency batch:** 15 sourced → 12 qualified → **4 drafted, 5 dropped,
  3 flagged**. Drops and flags are itemised with quoted evidence in `Leads/daily.log`.
  Electric Cat was dropped on-site ("we work exclusively with clients in and around the
  events industry"), overturning the tech-stack read that had called it the batch's
  strongest fit — the site settles what Apollo's stack column cannot.
- Tests: 64 + 53, green.

## Open question for Maha

§10 makes variant 3 proof-led, but §5 bars case studies from the agency pitch, so an
agency batch has no proof to lead with. Variant 3 is currently a capacity/process angle
— how the white-label engagement runs, no client names, no metrics. Say if you want it
handled differently.

## Blocking a live send

`SENDER_ADDRESS` is `30 Riverhead Close, Wales` — no town, no postcode. Not deliverable,
not CAN-SPAM valid. `send_approved.py` refuses to start until it is fixed. 13 of the 15
ecom leads are US/Canada.

---

# Previous state — 2026-08-12


Resume point. `README.md` is the runbook, `CLAUDE.md` is the spec. This file is just
"what happened last session and what's outstanding".

## Live right now

- **15 leads in the Outbox**, all `approve` blank, nothing sent to anyone.
  https://docs.google.com/spreadsheets/d/1KMtsezd_J5akQTeVWDO0DGp3gb4ibhK7pvIb97BAlxw/edit
  Each row: 3 email variants + LinkedIn connection note + LinkedIn DM + `icp_evidence`.
- **Daily cron armed**: `30 6 * * * scripts/daily_source.sh` → pull 15, alternate ICP by
  day-of-year parity (even=ecom, odd=agency), qualify, then draft via `claude -p`.
  Logs to `Leads/daily.log`.
- **Claude Code CLI 2.1.228** installed at `~/.local/bin/claude`. Headless verified
  working. The cron script resolves that path directly, so cron's minimal PATH is fine.
- **Apollo** working: `mixed_people/api_search` (free teaser) → `people/match`
  (1 credit each, returns email + revenue + employees + tech).
- **Gmail send verified live** — a test message was delivered to maha@mailitbetterco.com.

## Blocking, needs Maha

1. **`SENDER_ADDRESS` is incomplete.** Currently `30 Riverhead Close, Wales` — no town,
   no postcode, so it is not a deliverable address and does not satisfy CAN-SPAM.
   13 of the 15 leads are US/Canada. `send_approved.py` will send once a variant is
   approved, so this is the last thing standing between an approval and a live email.
2. **The first unattended drafting run has not happened yet.** Headless mode is proven
   to run; a full research-and-draft cycle has not been watched end to end. Check
   `Leads/daily.log` after the first 06:30 run and tune `scripts/daily_draft_prompt.md`
   against what actually happened.

## Not built

- Reply classification (replies stop a sequence; sorting them is manual).
- Email verification (NeverBounce/ZeroBounce) — relies on Apollo's `Email Status`
  plus the MX check.
- `--watch` mode exists on `send_approved.py` but is not running and has no cron.

## Bugs fixed this session (all have regression tests)

| Bug | Why it mattered |
|---|---|
| Conditional formats coloured each row by the row *below* it | `INSERT_ROWS` shifts relative refs; fixed with `INDEX(...,ROW())` |
| `format_tabs` stacked duplicate rules (15 instead of 5) | Stale rules pointed at columns that had since moved |
| Approve/li_status dropdowns stripped on every push | `INSERT_ROWS` creates bare rows; a free-text approve cell means a typo reads as "do nothing" |
| `has_mx` condemned live domains on DNS timeouts | `dig +short` prints nothing *and exits 0* on SERVFAIL. Now reads response status; only NXDOMAIN rejects |
| `--allow-role-emails` never reached `qualify()` | The flag silently did nothing |
| `sending_address()` had no retry | First network call of every send run; a blip killed an unattended loop before any message was built |
| `apollo_pull --limit` under-delivered | Exclusion filter ran *after* truncating to the limit — asked for 30, got 10 |
| Leads tab stuck on "Drafted, pending review" | CRM never reflected Sent/Replied/Opted Out |
| `known_companies()` only read `master-list.csv` | That is written on *send*, so daily runs would re-buy unsent leads. Added `sourced-ledger.csv` |

## Judgement calls worth remembering

- **Four leads were dropped during research that the filters passed**: Blanka (B2B
  white-label), Fluum and Daash (B2B SaaS, "Book a Demo"), pick'em (German-language
  site). Industry labels alone do not catch these — reading the site does.
- **Roughly 40-60% of qualified leads reach a genuine signal.** Sites 403, rate-limit,
  or do not resolve. A 15-lead pull yields ~6-9 drafted rows. That is the correct
  outcome, not a shortfall — CLAUDE.md §9 says flag, never invent.
- **I was wrong about Urbani Truffles** early on: reported it as having no MX and
  dropped it, on a transient DNS failure. It is fine. That is what prompted the
  fail-open MX rewrite.
