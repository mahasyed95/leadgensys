# Agency (White-Label) Sequence — Skeleton

Different buyer, different pitch. An agency owner isn't buying better email — they're
buying **capacity they don't have to hire for**. Never reuse ecom framing here.

**Critical rule (CLAUDE.md §5): do not name specific client work to agency prospects.**
An agency evaluating you as a white-label partner needs to know you'll be discreet about
*their* clients. Namedropping another client's brand signals the opposite. Describe
proof in terms of process, capacity, and turnaround — not brand names.

Every lead gets **three variants** written from the same research. Maha picks one and
that one sends. All three must open cold — none may read as a follow-up, because any of
them might be the only email this person ever gets.

---

## Variant 1 — The service gap
**Subject:** 3-6 words. Plain. e.g. "email for your clients"

**Body** (under 120 words, zero links):
1. The signal — what you observed on their site. Usually: they list paid media / branding /
   web, and email-SMS is conspicuously absent, or listed but clearly not a real offering.
2. The implication, stated neutrally — clients ask for it, and it's either turned down or
   passed to someone who does it as a sideline.
3. Soft CTA. "Worth a conversation?" / "Open to how that'd work?"

Don't assume they're losing money. State what's observable, let them fill in the rest.

---

## Variant 2 — How white-label actually works
The real objection for this buyer is *risk*, not interest — they're wondering what
happens to their client relationship.

Address it head-on as the opener: we work under their brand, they keep the client
relationship and the margin, we handle execution. Name the operational fact that
de-risks it (turnaround, communication flow, who's client-facing).

Under 120 words, same single CTA.

---

## Variant 3 — Capacity, concretely
Proof for this ICP = **process and range**, not a logo reel.

Lead with what running the channel for them looks like: channels covered (email, SMS,
WhatsApp), the shape of a typical engagement, turnaround, how many client accounts can
run in parallel. Portfolio work in `Assets/` can be shown *if they ask*, framed as
anonymized examples of design standard — not as "here's who we work with."

---

## LinkedIn — the primary channel (hand-sent, always)

Agency owners live on LinkedIn far more than ecom founders do, so for this ICP it is
not just primary, it is usually the only channel that gets read. Claude drafts; **Maha
sends by hand and maintains `li_status`.** Nothing automates this channel.

**Connection note** — hard limit **300 characters**, enforced at push time.
- Reference their agency's actual focus and the service gap. One line.
- **No pitch.** An agency owner receives white-label pitches constantly; the ones that
  land read like a peer noticing something, not a vendor opening.
- Do not name another agency's client work, here or anywhere (Section above).

**Follow-up DM** — after they accept, 2-3 sentences on the capacity angle.
- Lead with the risk they actually care about: what happens to their client
  relationship. Not the discount, not the turnaround.
- One CTA. "Open to how that'd work?" beats a calendar link.

---

## Stop conditions
Any reply on any channel → the sender cancels the row automatically; cancel LinkedIn touches by hand.
Opt-out or complaint → run `scripts/record_sent.py --suppress <email>` same day.
