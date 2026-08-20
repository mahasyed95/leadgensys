#!/usr/bin/env python3
"""
Push drafted messages into the Google Sheet for approval.

Claude writes drafts as JSON into Sequences/Drafts/. This loads them and appends one
Outbox row per lead, carrying three different versions of the message. Nothing sends
until you type 1, 2 or 3 into that row's `approve` column.

    .venv/bin/python scripts/push_to_sheet.py Sequences/Drafts/2026-08-12-ecom.json

Draft JSON shape — exactly three variants, each a genuinely different angle:
[
  {
    "email": "sarah@olivea.com", "first_name": "Sarah", "company": "Olivea",
    "icp": "ecom", "domain": "olivea.com", "title": "Founder",
    "signal": "welcome flow buries the CTA under the hydroxytyrosol explainer",
    "variants": [
      {"subject": "your welcome flow", "body": "..."},
      {"subject": "the SMS gap",       "body": "..."},
      {"subject": "Olivea's first 48h", "body": "..."}
    ]
  }
]
"""

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets  # noqa: E402
from process_batch import FREEMAIL, registrable_domain  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_drafts(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else [data]


def validate(lead, idx):
    """Refuse to push a draft that would send something broken or unpersonalized."""
    problems = []
    who = lead.get("email", "?")
    for field in ("email", "first_name", "variants"):
        if not lead.get(field):
            problems.append(f"lead {idx}: missing '{field}'")
    if not lead.get("signal"):
        # CLAUDE.md Section 9: no genuine signal means the lead gets flagged, not sent.
        problems.append(f"lead {idx} ({who}): no personalization signal recorded")

    variants = lead.get("variants") or []
    if variants and len(variants) != len(sheets.VARIANTS):
        # The point of this layout is choosing between three. Two leaves a blank
        # column that reads as a bug; four silently loses one.
        problems.append(
            f"lead {idx} ({who}): {len(variants)} variants, expected exactly {len(sheets.VARIANTS)}")
    for n, v in enumerate(variants[:len(sheets.VARIANTS)], 1):
        if not (v.get("subject") or "").strip() or not (v.get("body") or "").strip():
            problems.append(f"lead {idx} ({who}) variant {n}: empty subject or body")

    subjects = [(v.get("subject") or "").strip().lower() for v in variants]
    bodies = [(v.get("body") or "").strip().lower() for v in variants]
    if len(set(bodies)) < len([b for b in bodies if b]):
        problems.append(f"lead {idx} ({who}): two variants have identical bodies — "
                        f"three copies of one draft is not a choice")
    elif len(set(subjects)) < len([s for s in subjects if s]):
        problems.append(f"lead {idx} ({who}): two variants share a subject line")

    # LinkedIn is the primary channel (CLAUDE.md Section 6), so a lead with a profile
    # must arrive with LinkedIn copy — otherwise the main channel has nothing to send.
    li = lead.get("linkedin") or {}
    if lead.get("linkedin_url"):
        note = (li.get("note") or "").strip()
        if not note:
            problems.append(f"lead {idx} ({who}): has a LinkedIn profile but no connection note")
        elif len(note) > sheets.LI_NOTE_LIMIT:
            problems.append(f"lead {idx} ({who}): LinkedIn note is {len(note)} chars, "
                            f"limit is {sheets.LI_NOTE_LIMIT}")
        if not (li.get("dm") or "").strip():
            problems.append(f"lead {idx} ({who}): has a LinkedIn profile but no follow-up DM")
    return problems


def lead_domain(email):
    """Company domain for an address, or "" for a personal mailbox.

    Freemail is excluded deliberately: two prospects who both happen to use gmail.com
    are not the same company, and treating them as one would drop real leads.
    """
    host = (email or "").strip().lower().split("@")[-1]
    if not host or host in FREEMAIL:
        return ""
    return registrable_domain(host)


def outbox_domains(rows=None):
    """Company domains already represented in the Outbox."""
    rows = sheets.read(sheets.OUTBOX) if rows is None else rows
    return {d for d in (lead_domain(r.get("email", "")) for r in rows) if d}


def icp_evidence(lead):
    """One-line proof of why this lead is in the batch, shown next to the ICP tag so
    fit is visible on the row instead of buried in a CSV."""
    bits = [lead.get("icp", "") or "?"]
    if lead.get("employees"):
        bits.append(f"{lead['employees']} staff")
    if lead.get("revenue"):
        # A hand-written drafts JSON can carry "12M" or "" where the Apollo path always
        # supplies an int. A crash here would reject an otherwise valid batch over a
        # cosmetic column, so fall back to printing the value as given.
        try:
            bits.append(f"${int(float(lead['revenue'])) / 1_000_000:.0f}M")
        except (TypeError, ValueError):
            bits.append(str(lead["revenue"]))
    if lead.get("industry"):
        bits.append(lead["industry"])
    if lead.get("country"):
        bits.append(lead["country"])
    return " · ".join(str(b) for b in bits if b)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("drafts_json")
    ap.add_argument("--dry-run", action="store_true", help="print what would be pushed, touch nothing")
    args = ap.parse_args()

    if not os.path.exists(args.drafts_json):
        raise SystemExit(f"Not found: {args.drafts_json}")

    leads = load_drafts(args.drafts_json)
    problems = []
    for i, lead in enumerate(leads, 1):
        problems.extend(validate(lead, i))
    if problems:
        print("\nRefusing to push — fix these first:\n")
        for p in problems:
            print(f"  {p}")
        raise SystemExit(1)

    outbox_rows, lead_rows = [], []
    for lead in leads:
        email = lead["email"].strip().lower()
        lead_rows.append({
            "email": email,
            "domain": lead.get("domain", ""),
            "company": lead.get("company", ""),
            "first_name": lead.get("first_name", ""),
            "title": lead.get("title", ""),
            "icp": lead.get("icp", ""),
            "batch_id": os.path.splitext(os.path.basename(args.drafts_json))[0],
            "signal": lead.get("signal", ""),
            "status": "Drafted, pending review",
            "date_added": f"{date.today():%Y-%m-%d}",
        })
        li = lead.get("linkedin") or {}
        profile = lead.get("linkedin_url", "")
        row = {
            # One row per lead now, so the address alone identifies it.
            "row_key": email,
            "email": email,
            "first_name": lead.get("first_name", ""),
            "company": lead.get("company", ""),
            "icp": lead.get("icp", ""),
            "icp_evidence": icp_evidence(lead),
            "channel": "linkedin+email" if profile else "email-only",
            "linkedin_url": profile,
            "li_note": li.get("note", ""),
            "li_dm": li.get("dm", ""),
            # li_status is deliberately absent. CLAUDE.md Sections 12 and 13 reserve it
            # for Maha — "nothing in this repo writes it, because nothing in this repo
            # touches LinkedIn" — and this line used to seed it with "To send" directly
            # beneath a comment claiming nothing ever wrote it. Seeding a value is
            # harmless in itself, but an invariant that the code quietly breaks is not
            # an invariant, and this one guards the channel with the ban risk. The
            # dropdown still offers "To send"; blank reads the same way.
            "signal": lead.get("signal", ""),
            # Left empty on purpose. This is the approval gate, and Claude writing
            # anything here would be forging Maha's signature on the message.
            "approve": "",
            "status": sheets.DRAFT,
            "sent_variant": "", "sent_at": "", "thread_id": "",
            "rfc_message_id": "", "note": "",
        }
        for n, v in zip(sheets.VARIANTS, lead["variants"]):
            row["subject_%d" % n] = v["subject"]
            row["body_%d" % n] = v["body"]
        outbox_rows.append(row)

    if args.dry_run:
        print(f"\nDRY RUN — would push {len(lead_rows)} leads, "
              f"{len(outbox_rows) * len(sheets.VARIANTS)} drafts across {len(outbox_rows)} rows\n")
        for r in outbox_rows[:6]:
            print(f"  {r['email']}  [{r['channel']}]  {r['icp_evidence']}")
            print(f"    signal: {r['signal'][:88]}")
            if r["li_note"]:
                print(f"    LI note ({len(r['li_note'])}c): {r['li_note'][:76]}")
            for n in sheets.VARIANTS:
                print(f"    {n}. {r['subject_%d' % n][:44]:46} {r['body_%d' % n][:60]!r}")
        if len(outbox_rows) > 6:
            print(f"  ... and {len(outbox_rows) - 6} more leads")
        print()
        return

    sheets.ensure_tabs()
    # One read, two derived sets. existing_row_keys() would re-read the same tab.
    outbox_now = sheets.read(sheets.OUTBOX)
    existing = {(r.get("row_key") or "").strip() for r in outbox_now if r.get("row_key")}
    # Domain-level, not just email-level. CLAUDE.md Section 8 calls this a standing
    # rule: two contacts at one company count as a duplicate, because reaching two
    # people at the same company at once reads as spam and burns the sending domain.
    # Checking row_key alone let alice@acme.com and bob@acme.com both through — the
    # Apollo path happens to catch it via sourced-ledger.csv, but a hand-written or
    # re-drafted JSON goes straight past that.
    existing_domains = outbox_domains(outbox_now)

    # Dedupe within this file too — the same lead appearing twice in one drafts JSON
    # would otherwise produce two identical Outbox rows and two identical emails.
    fresh, seen_keys, seen_domains = [], set(), set()
    dup_company = []
    for r in outbox_rows:
        if r["row_key"] in existing or r["row_key"] in seen_keys:
            continue
        dom = lead_domain(r["email"])
        if dom and (dom in existing_domains or dom in seen_domains):
            dup_company.append((r["email"], dom))
            continue
        seen_keys.add(r["row_key"])
        if dom:
            seen_domains.add(dom)
        fresh.append(r)
    skipped = len(outbox_rows) - len(fresh) - len(dup_company)

    known = {(r.get("email") or "").strip().lower() for r in sheets.read(sheets.LEADS)}
    new_leads = [r for r in lead_rows if r["email"] not in known]

    sheets.append(sheets.LEADS, new_leads)
    sheets.append(sheets.OUTBOX, fresh)

    # Re-assert the dropdowns and colours over the rows just written. Belt and braces:
    # append no longer inserts rows (which was what stripped them), but if the grid
    # ever runs out of spare rows Sheets will extend it, and an extended row starts
    # with no validation. A lead whose approve cell is free text is a lead whose
    # approval can be typo'd into silence.
    if fresh:
        try:
            sheets.format_tabs()
        except Exception as e:
            print(f"!  Rows written, but re-applying the dropdowns failed: {str(e)[:140]}")
            print("   Run: .venv/bin/python scripts/sheets.py --format")

    print(f"\nPushed {len(fresh)} rows ({len(fresh) * len(sheets.VARIANTS)} drafts) "
          f"for {len(new_leads)} new leads.")
    if dup_company:
        print(f"Skipped {len(dup_company)} lead(s) whose company is already in the Outbox "
              f"— one person per company at a time (CLAUDE.md Section 8):")
        for email, dom in dup_company:
            print(f"  {email}  ({dom})")
    if skipped:
        # One Outbox row per person, ever — that is what stops the same lead being
        # mailed twice from two different batches. Re-drafting is deliberate work.
        print(f"Skipped {skipped} already in the Outbox — one row per lead is the rule "
              f"that prevents double-contacting.")
        print("To re-draft one of them, delete their existing Outbox row first.")
    print("\nNothing emails until you pick an 'Approve Draft' option in `approve`.")
    print("LinkedIn is in li_note / li_dm on each row — send those by hand.")
    print(f"https://docs.google.com/spreadsheets/d/{sheets.sheet_id()}/edit\n")


if __name__ == "__main__":
    main()
