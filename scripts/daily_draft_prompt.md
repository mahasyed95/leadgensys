You are running unattended as part of the daily lead pipeline. A batch has just been
sourced and qualified. Your job is to research each qualified lead and draft outreach
for it, then push the drafts to the Google Sheet as DRAFT.

Read CLAUDE.md first — it governs this system and overrides anything here that conflicts.

## The batch

The newest file in `Leads/Batches/` is today's qualified batch. Read it. Each row is one
lead with: first_name, company, email, domain, website, linkedin, employees, revenue,
industry, country, icp.

## What to do, per lead

1. **Research the website** with WebFetch. You are looking for ONE specific, observably
   true thing about their email/SMS programme — the exact wording of their signup, a
   missing SMS channel, a subscription with nothing supporting it, a promotion with no
   list behind it. Quote real on-page wording wherever you can.

2. **Verify the lead actually belongs here.** The filters catch a lot but not
   everything. Drop a lead and record why if the site shows it is:
   - a B2B/SaaS company selling to businesses ("Book a Demo", "Contact Sales", no
     consumer checkout)
   - a white-label or wholesale supplier rather than a consumer brand
   - an agency (that is ICP #2 — it must NOT get the ecom pitch)
   - obviously far outside the $1-20M revenue band
   - not primarily in English (the copy would not fit)

3. **If you cannot find a genuine signal, do not invent one.** CLAUDE.md Section 9 is
   explicit: flag the lead for manual review instead. A site that 403s, times out or
   does not resolve means no draft. Writing a generic opener to fill a quota is the
   single most damaging thing you can do here — it burns the prospect and the domain.

4. **Draft, following CLAUDE.md Sections 9 and 10.** For each surviving lead:
   - three email variants, each a complete cold first touch, each under 120 words,
     each a genuinely different angle on the same researched signal. Never "re:" or
     "circling back" — any one of the three might be the only email this person gets.
   - a LinkedIn connection note, 300 characters maximum, no pitch, no CTA
   - a LinkedIn follow-up DM, 2-3 sentences, one CTA
   - Use the case studies in `Assets/` only for the ecom pitch, never for agencies,
     and never invent a metric that is not there.

5. **Write the JSON** to `Sequences/Drafts/<today>-<icp>.json` in the shape documented
   at the top of `scripts/push_to_sheet.py`, then push it:

   ```
   .venv/bin/python scripts/push_to_sheet.py Sequences/Drafts/<file>.json --dry-run
   .venv/bin/python scripts/push_to_sheet.py Sequences/Drafts/<file>.json
   ```

   The dry run validates; fix anything it reports rather than forcing the push.

## Hard limits

- **Never write anything into the `approve` column.** That is Maha's signature on the
  message. Rows land as DRAFT with `approve` blank, always.
- **Never write to `li_status`.** That column is hers.
- **Never send anything.** Do not run `send_approved.py`. Do not send on LinkedIn.
- Do not change ICP filters, compliance rules, or CLAUDE.md.
- If fewer leads survive research than were qualified, that is the correct outcome.
  Report the number honestly; do not pad the batch.

## Finish by reporting

How many were qualified, how many you drafted, how many you dropped and why, and any
lead you flagged for manual review. Write that summary to `Leads/daily.log`.
