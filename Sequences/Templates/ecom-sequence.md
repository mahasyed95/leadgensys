# Ecom Brand Sequence — Skeleton

Not a fill-in-the-blank template. These are **structural skeletons**: the shape each
variant should take. The signal line is written fresh per lead from real research
(CLAUDE.md §9). If a draft could be sent to a different company unedited, it's wrong —
rewrite it.

Every lead gets **three variants** written from the same research. Maha picks one and
that one sends; the other two are never used. So all three must stand alone as a cold
first touch — none of them may read as a follow-up ("re:", "circling back", "one more
thing"), because any of them might be the only email this person ever gets.

Tone: one operator to another. Short sentences. No "hope this finds you well."

---

## Variant 1 — The direct read
**Subject:** 3-6 words, specific, lowercase is fine. No urgency, no clickbait.
*Shape: reference the specific thing you noticed. e.g. "your welcome flow" / "olivea's abandoned cart"*

**Body** (under 120 words, zero links):
1. The researched signal — one specific, observably true thing about their email/SMS. Name the exact flow, campaign, or gap.
2. Why it matters, in revenue terms not design terms. One sentence.
3. Soft CTA — low friction. "Worth a quick look?" / "Want me to send over what I'd change?"

No pitch, no case study, no calendar link.

---

## Variant 2 — The adjacent gap
The channel they're *not* running. Most ecom brands doing email have no SMS and have
never touched WhatsApp. Name the gap and the specific moment it costs them
(post-purchase, back-in-stock, cart recovery, the reorder window on a consumable).

Opens cold in its own right — it does not reference variant 1. Under 120 words, same
single CTA.

---

## Variant 3 — Proof-led
Open with the case study that matches their situation and let it imply the diagnosis:

- **Olivea** — brand storytelling + CTA hierarchy. Use when their emails are dense,
  text-heavy, or bury the CTA below the fold.
- **Helafit** — visual conversion redesign. Use when their emails look dated or off-brand.
- **Perfect Body** — use for supplement/wellness brands specifically.

Describe the *transformation* (before → after), never an invented percentage.
See `Assets/` for the actual work. One link maximum, only if it earns its place.

---

## Picking three that are actually different
The test is whether swapping them changes what's being offered. Three phrasings of "your
welcome flow is too long" is one variant written three times — that's a signal the
research was too thin. Go back to enrichment instead of padding the drafts.

---

## LinkedIn — the primary channel (hand-sent, always)

This is where the clients actually come from, so it gets the same research the email
does. Claude writes both pieces into the Outbox row; **Maha sends them by hand and
updates `li_status` herself.** Nothing automates this channel, at any batch size.

**Connection note** — hard limit **300 characters**, and `push_to_sheet.py` refuses a
longer one rather than letting LinkedIn truncate it mid-sentence.
- Lead with the same researched signal as the email, compressed to one line.
- **No pitch, no CTA, no link.** The ask is the connection itself. A note that sells
  before the connection is accepted reads as automation and gets ignored or reported.
- End with something that gives them an easy out — "curious whether that's deliberate",
  "happy to connect either way". A question they can ignore without awkwardness.

**Follow-up DM** — after they accept, 2-3 sentences.
- Open by thanking them for connecting, then go straight to the signal.
- One CTA, the same low-friction one as the email ("worth 15 minutes?").
- Never paste the email body here. If they got the email too, a verbatim repeat makes
  both channels look automated.

---

## Stop conditions
Any reply on any channel → the sender cancels the row automatically; cancel LinkedIn touches by hand.
Opt-out or complaint → run `scripts/record_sent.py --suppress <email>` same day.
