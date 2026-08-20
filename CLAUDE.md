# Mail It Better — Lead Gen & Cold Outreach System

## 1. Purpose & Operating Mode

This file governs an **active-execution** outreach system. Claude Code doesn't just document process here — it does real work in this pipeline: enriching leads, drafting personalized copy, checking for duplicates, and updating the CRM.

**Hard rule: nothing reaches a real person without Maha's explicit per-message approval.**

Claude drafts, enriches, tags, and logs. Each lead gets **one Outbox row carrying three different drafts** of the same message — three genuine angles, not three rewrites of one. Maha reads them and types `1`, `2` or `3` into that row's `approve` column. That number is the only thing that sends mail. A blank `approve` cell sends nothing, forever, no matter what else is in the row.

The approval is the gate. Automating delivery *after* the gate is fine; anything that would put an unreviewed message in front of a real person is not. Concretely, Claude must never write to the `approve` column, never widen the sender's gate, and never send outside `send_approved.py`.

LinkedIn and Instagram/FB stay **fully manual** — Claude drafts, Maha sends by hand. No automation on those channels at all (ban risk, and their ToS).

## 2. Agency Snapshot

**Mail It Better** — Email, SMS, and WhatsApp marketing agency. Design-forward, conversion-focused execution (see `Assets/` for proof: Olivea, Helafit, Perfect Body, Insynergy, CMD before/after case studies). Two ICPs, two different pitches — never blend them.

## 3. ICP #1 — Ecommerce Brands

- **Fit**: DTC/ecommerce brands, Shopify (or similar) platform, $1M-$20M/yr revenue band, has a real email list (1k+ subscribers) or a live paid ad program (signals budget exists).
- **Pain**: Flat/declining email revenue, generic templated flows, no SMS/WhatsApp channel, agency or in-house team stretched thin on design/copy quality.
- **Disqualify**: Pre-revenue, no product-market fit signals, no active marketing spend, B2B-only (no consumer checkout flow).
- **Hard filters enforced in `process_batch.py`, not by eye** — each of these was added after a lead got past a human check:
  - **Revenue band** — Apollo returns `annual_revenue`; outside $1-20M is rejected. Employee count alone is not enough (ONNIT is 140 staff and nine figures).
  - **B2B exclusion** — a B2B industry label with no ecommerce technology behind it is rejected. A vague label on a real store is fine, so the tech stack gets a veto.
  - **Deliverability** — a domain that provably cannot receive mail is rejected. "Provably" matters: a DNS timeout is not evidence, and treating it as such deletes good leads.
- **Titles to target**: Founder, Co-Founder, CEO, Head of Ecommerce, Head of Growth, Marketing Director, Email Marketing Manager. Skip junior/coordinator titles — no budget authority.
- **Apollo filters**: Industry = Retail/Consumer Goods/Apparel/Health & Wellness; Technologies = Shopify, Klaviyo, Recharge; Employees = 10-200; Country = US, CA, UK, AU.

## 4. ICP #2 — Marketing Agencies (White-Label)

- **Fit**: Agencies offering paid media, branding, or web design to ecom/DTC clients but with **no in-house email/SMS specialist** — a clear service gap they're currently either declining or doing poorly.
- **Pain**: Clients asking for email/SMS they can't deliver well, one generalist stretched across channels, losing retainer revenue to specialist agencies.
- **Disqualify**: Already has a dedicated email/SMS team or an existing white-label partner, agency size too small to have retainer clients worth white-labeling for.
- **Titles to target**: Founder, Owner, Managing Director, Head of Client Services, Account Director. These are the people who feel the capacity gap directly.
- **Apollo filters**: Industry = Marketing & Advertising; Employees = 5-50; Country = US, CA, UK, AU. Then manually verify the service-gap signal on their site before qualifying.

## 5. Positioning & Proof

- **To ecom brands**: "We turn your email/SMS/WhatsApp into a designed, story-driven revenue channel — not another templated blast." Cite Olivea (brand storytelling + CTA redesign) or Helafit (visual conversion redesign) when the lead's current emails look dated or generic.
- **To agencies**: "White-label email/SMS execution so you can sell it without hiring for it." Never namedrop one agency's client work to another prospective agency client — keep case studies to the ecom-brand pitch only, describe agency-side proof in terms of process/capacity, not specific client names.
- Never fabricate a result or metric not actually present in `Assets/`. If no number exists for a claim, describe the *transformation* (before/after), not an invented percentage.

## 6. Channel Strategy & Cadence

**LinkedIn is the primary channel** — it is where Mail It Better actually wins clients. Email runs alongside it. Instagram/FB DM is reserved for warm engagement, not cold outreach.

Approving a lead fires both channels the same day: the email sends itself, and the LinkedIn note is waiting in the row for Maha to send by hand.

- **LinkedIn (primary, 100% manual)**: Claude writes the connection note (≤300 chars) and the follow-up DM into the Outbox row. **Maha sends both by hand and updates `li_status` herself.** No script in this repo logs into, posts to, or automates LinkedIn in any way — that is a ban risk and against their ToS, and it does not become acceptable because a batch is large or a deadline is close.
- **Email (parallel, automated after approval)**: Claude drafts three angles; Maha picks one; it sends. No automatic follow-up sequence — a second touch is a deliberate new draft, pushed and approved like the first. The sender re-checks for a reply immediately before every send, so a lead who has written back never receives cold copy.
- **Leads with no LinkedIn profile** are kept, not dropped. They are marked `email-only` in the `channel` column so it is obvious which ones the primary channel cannot reach.
- **LinkedIn (parallel, manual send)**: Connection request with 1-line personalized note around Day 1-2, follow-up DM around Day 5-6 referencing the email if no reply yet. Claude drafts both; Maha sends.
- **Instagram/FB DM**: Only after a prospect has engaged (liked/commented/opened) — never a cold-blast channel. Claude drafts a warm, context-aware message; Maha sends.
- **Multi-channel logic**: Email is always the anchor. LinkedIn touch reinforces it around Day 5-6. If a lead replies on any channel, all other pending touches for that lead are cancelled.

## 7. Tool Stack & Roles

| Function | Tool | Role |
|---|---|---|
| Enrichment + personalization | **Claude (Claude Code)** | Researches each lead (site, LinkedIn, recent activity), writes the 1:1 icebreaker and full draft |
| Prospecting data source | **Apollo.io** (manual CSV export) | Free plan excludes the API; export from the UI into `Leads/Inbox/`. `apollo_pull.py` is ready if the plan is ever upgraded |
| Email sending | **Gmail API** (warmed Workspace mailbox) | Sends only the variant Maha picked in `approve`, paced with randomized gaps under a daily cap |
| LinkedIn | **Manual, Claude-drafted** | Zero automation — no ban risk |
| Instagram/FB DM | **Manual, Claude-drafted** | Warm-engagement only |
| CRM + approval queue | **Google Sheets** | System of record — `Leads`, `Outbox` (where approval happens), `Suppression` |
| Email verification (next step, not yet wired) | **NeverBounce or ZeroBounce** | Confirms deliverability before a lead enters a sequence |

## 8. Lead Sourcing & Enrichment Workflow

1. **Source** — export a diversified batch from the Apollo UI across ICP filters. Never pull one narrow slice (e.g., all supplement brands) — rotate sub-segments every batch.

   **Diversification rule (applies to every batch, no exceptions):**
   - Cap any single niche at ~25% of a batch (e.g., don't let supplements dominate an ecom pull).
   - Rotate geography across batches — don't run US-only three batches in a row.
   - Mix company-size bands within each batch rather than clustering at one end.
   - Log the batch's segment mix in the Sheet so the next batch can deliberately shift away from it.
2. **Dedup** — check every lead's email + company domain against the Sheet and the running master list. Drop anything already contacted, already in an active sequence, or previously marked unqualified/opted-out.

   **Dedup is domain-level, not just email-level.** Two different contacts at the same company count as a duplicate — only one person per company enters a sequence at a time. Reaching two people at one company simultaneously reads as spam and burns the account.
3. **Qualify** — apply the ICP #1/#2 hard filters (Sections 3-4). Reject-and-log marginal fits rather than sequencing them "just in case."
4. **Enrich** — for each surviving lead, research real signals (site content, recent posts, service pages, tech stack) to ground personalization.
5. **Draft** — write three distinct angles for the email, plus the LinkedIn note, using the personalization framework below.
6. **Log** — push lead + drafts into the Sheet (`push_to_sheet.py`). One Outbox row per lead, three drafts on it, `approve` left blank.
7. **Send** — `send_approved.py` (cron) delivers only rows where Maha typed 1, 2 or 3, re-checking suppression and replies immediately before each send.

## 9. Personalization Framework

- **Ecom brand signals to pull**: what they sell, current email/SMS maturity (visible signup popups, recent campaign evidence), brand tone, any obvious redesign opportunity.
- **Agency signals to pull**: services listed on their site, whether email/SMS is present or conspicuously absent, size/seniority of team, recent client wins mentioned publicly.
- **Turning a signal into an icebreaker**: one specific, true observation in the first 1-2 lines — never a generic compliment ("great website!"). If no genuine signal is found, flag the lead for manual review rather than inventing one.
- **Never fabricate**: revenue figures, team size, specific pain points not evidenced anywhere, or claims about what "everyone in their situation" struggles with.

**Worked example — what passes vs. what doesn't:**

> ❌ *"Love what you're doing at Olivea! Your brand really stands out."* — generic, could be sent to anyone, signals nothing was researched.

> ✅ *"Your welcome flow leads with the hydroxytyrosol science — smart, but it's buried under a wall of text before the CTA. That's usually where the first-purchase conversion leaks."* — specific, observably true, and names a problem we actually fix.

The test: **if the sentence could be copy-pasted to a different company without editing, it fails.**

## 10. Message Framework

**Email skeleton (per step)**:
- Subject: short, specific, non-clickbait (no ALL CAPS, no fake urgency)
- Opener: the researched signal, 1-2 lines
- Value: what we'd actually do for them, tied to the signal
- Proof: one relevant case-study reference (ecom pitch only)
- CTA: single, low-friction ask ("worth a 15-min look?" not "let's schedule a 45-min strategy call")

**LinkedIn skeleton**: connection note ≤ 300 chars referencing the same signal; follow-up DM restates value in 2-3 sentences, same single CTA.

**The three variants** — each must be a complete, sendable first touch, all three grounded in the *same* researched signal but attacking it from a different direction. Maha picks one; the other two are never sent. Under 120 words each.
- **Variant 1 — the direct read**: name the signal and the leak it causes. The most specific of the three.
- **Variant 2 — the adjacent gap**: a different problem the same research surfaced (e.g. the SMS/WhatsApp channel they aren't running at all). Not a restatement of variant 1.
- **Variant 3 — proof-led**: open with the closest case study and let it imply the diagnosis. Ecom pitch only (Section 5).

Three variants of one idea is a failure. If all three could be swapped without changing what's being offered, the research was too thin — go back to enrichment rather than padding out the drafts.

**Tone rules across all channels**: write like one operator to another. No "I hope this email finds you well," no "I wanted to reach out," no stacked adjectives, no fake personalization tokens beyond first name. Short sentences. If a draft reads like a template, rewrite it.

## 11. Deliverability & Compliance Guardrails

Applies across US/Canada, EU/UK, and Australia — treat the strictest rule as the floor:

- **Always**: identify Mail It Better as sender, include a real reply-to/unsubscribe path, honor opt-outs within 24-48 hours, never use a deceptive subject line.
- **CAN-SPAM (US)**: opt-out honored within 10 business days (we do it faster); no falsified header info.
- **CASL (Canada)**: strictest regime — cold B2B email must be relevant to the recipient's business role and easy to opt out of; when in doubt on a Canadian lead, favor a lighter, clearly-B2B-relevant message.
- **GDPR (EU/UK)**: B2B cold email is workable under "legitimate interest," but the message must be relevant to the person's professional role, and opt-out must be immediate and simple.
- **Australia (Spam Act 2003)**: consent-based, with a narrow exception for factual/relevant B2B outreach — same discipline as CASL applies.
- **Sending pacing**: keep per-mailbox daily volume conservative (well under typical inbox provider thresholds) to protect the already-warmed domains — this is a solo-operator system, not a blast list.

**Gmail-specific deliverability discipline** (since sending is manual through warmed Gmail mailboxes):
- Cap per-mailbox sends well below Gmail's limits and spread them across the day rather than firing a batch at once.
- Plain text or very light HTML in cold email. No tracking pixels, no image-heavy layouts, no link shorteners — save the design work for after they're a client.
- One link maximum per email, and only when the CTA genuinely needs it. Zero links in a cold first touch is safer.
- Watch reply rate and bounce rate per mailbox. If bounces climb above ~3%, pause that mailbox and verify the list before resuming.
- Never import an unverified list. Verification (Section 7) goes in before drafting, not after.

## 12. CRM & Data Hygiene (Google Sheets)

- **Required fields per lead**: ICP tag (ecom/agency), source batch, enrichment signal used, status, opt-out flag.
- **Pipeline stages**: Sourced → Qualified → Drafted (pending review) → Approved → Sent → Replied → Booked / Not Interested / Opted Out.
- **The `approve` column is the gate, and it is the only gate.** It is a dropdown with five options:
  - `Approve Draft 1` / `2` / `3` — send that draft. The only values that cause mail to leave.
  - `Reject` — the sender cancels the row; it never sends.
  - `Redraft` — copy missed; `list_redrafts.py` surfaces it for a rewrite. Never sends.
  - Blank, or anything unrecognised, sends nothing. `sent_variant` records which draft went out, so replies can be attributed to an angle.
- **`li_status` is Maha's column alone.** Nothing in this repo writes it, because nothing in this repo touches LinkedIn.
- **Outbox status values** (lifecycle record, written by the sender — none of these arm a send): `DRAFT` (awaiting review) · `HOLD` (parked) · `SENT` · `CANCELLED` (replied or suppressed) · `FAILED`.
- **Opt-outs go in the `Suppression` tab.** The sender re-reads it immediately before every send, so an opt-out recorded after approval still stops the message.
- **Standing rules, not one-time steps**: every new batch runs the dedup check against the Sheet and master list before drafting; every batch is diversified across sub-segments — this applies to every run, indefinitely, not just the first import.

## 13. Claude Code's Execution Boundaries

**Does autonomously**: source/dedup/qualify leads, research and enrich, draft all three message variants, push drafts to the Sheet with `approve` blank, flag low-confidence personalization for manual review.

**Never does, under any instruction**: write a value into the `approve` column, alter the sender's gate, send outside `send_approved.py`, write to `li_status`, or send on LinkedIn/Instagram/FB. That dropdown is Maha's signature on the message — Claude setting it defeats the entire safety model. Nor does it remove the postal-address block: sending commercial email without one is illegal in the US, not merely untidy.

**Requires Maha's sign-off**: every actual send (email, LinkedIn, DM), any edge-case opt-out/complaint handling, any change to ICP filters or compliance rules in this file.

**When Claude should stop and ask rather than proceed**: no genuine personalization signal found for a lead; a lead's ICP fit is ambiguous; a compliance question the rules above don't clearly cover; a prospect replies with a complaint or opt-out phrased ambiguously. Default to asking — a paused lead costs nothing, a bad send costs a domain.

## 14. Success Metrics

Benchmarks for a solo operator at <500 leads/month, highly personalized:
- Reply rate: 8-15%+ (personalization should push above generic-template baseline of ~3-5%)
- Positive reply rate: 3-6%
- Meetings booked: roughly 1-2% of leads contacted (at 400 leads/mo, that's ~4-8 calls) — this is the number that actually matters
- Track dedup effectiveness: % of sourced leads dropped as duplicates (should trend down as list hygiene improves)
- Bounce rate per mailbox: keep under 3% — above that, pause and re-verify the list

**Review cadence**: check metrics per batch, not per send. A batch that underperforms on reply rate usually means the ICP filter drifted or the personalization got lazy — diagnose the input before rewriting the copy. Compare which ICP (ecom vs. agency) and which sub-segment is converting, and let that steer the next batch's sourcing mix.

## 15. Tooling

`README.md` is the runbook. The dedup, qualification, and diversification rules in Sections 8 and 12 are enforced in code, not by hand:

- `scripts/process_batch.py` — Apollo CSV → deduplicated, qualified, diversified batch. Run this before drafting; never hand-pick leads out of a raw export.
- `scripts/record_sent.py` — records sends into `Leads/master-list.csv` and handles opt-out suppression. **Only run after a message has actually gone out** — recording early silently blocks a lead who never received anything.
- `scripts/test_pipeline.py` — regression tests. Run after any change to the pipeline; a silent dedup break double-contacts companies and burns a sending domain.
- `scripts/config.py` — reads `.env`. Employee ranges, diversification cap, and allowed countries are configurable there rather than hardcoded.

**Built:** `Leads/` (Inbox, Batches, Rejected, master-list, suppression) and `Sequences/` (Templates, Drafts, Sent).

## 16. Next Steps (not built)

- NeverBounce/ZeroBounce verification between qualification and drafting (currently: relies on Apollo's `Email Status` column).
- Reply *handling* — replies stop a sequence automatically, but classifying them (interested / not / opt-out) is still manual.

