#!/usr/bin/env python3
"""
Record leads as contacted, or suppress them.

Only run this AFTER Maha has actually sent the outreach. A lead in master-list.csv
is treated as "already contacted" by every future batch, so recording early would
silently block a lead that never got a message.

Usage:
    # mark a whole batch as sent
    python3 scripts/record_sent.py --batch Leads/Batches/2026-08-08-ecom.csv

    # mark specific leads as sent
    python3 scripts/record_sent.py --email sarah@example.com dan@other.com

    # suppress (opt-out, bounce, not interested) — blocks all future contact
    python3 scripts/record_sent.py --suppress rob@example.com --reason "unsubscribed"
"""

import argparse
import csv
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
from process_batch import FREEMAIL, registrable_domain  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = config.get("OUTREACH_MASTER", os.path.join(ROOT, "Leads", "master-list.csv"))
SUPPRESSION = config.get("OUTREACH_SUPPRESSION", os.path.join(ROOT, "Leads", "suppression.csv"))


def domain_from_email(email):
    """Match the normalization process_batch.py dedups against, or the ledger entry
    won't block the company on the next batch."""
    host = email.split("@")[-1].strip().lower() if "@" in email else ""
    return "" if not host or host in FREEMAIL else registrable_domain(host)


MASTER_FIELDS = ["email", "domain", "company", "first_name", "icp", "batch_id",
                 "date_first_contacted", "status"]
SUPPRESSION_FIELDS = ["email", "domain", "company", "reason", "date_added"]


def record_send(email, domain="", company="", first_name="", icp="", batch_id="outbox"):
    """Append one lead to the master dedup ledger. Returns True if newly added.

    send_approved.py calls this immediately after a successful send. Without it the
    Sheet would know a lead was contacted but process_batch.py would not, so the next
    Apollo pull would re-source and re-contact the same company.
    """
    email = (email or "").strip().lower()
    if not email:
        return False
    rows = load(MASTER, MASTER_FIELDS)
    if any((r.get("email") or "").strip().lower() == email for r in rows):
        return False
    rows.append({
        "email": email,
        "domain": domain or domain_from_email(email),
        "company": company,
        "first_name": first_name,
        "icp": icp,
        "batch_id": batch_id,
        "date_first_contacted": f"{date.today():%Y-%m-%d}",
        "status": "Sent",
    })
    save(MASTER, MASTER_FIELDS, rows)
    return True


def load(path, fields):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def save(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch", help="path to a processed batch CSV to mark as sent")
    ap.add_argument("--email", nargs="+", help="specific emails to mark as sent")
    ap.add_argument("--suppress", nargs="+", help="emails to suppress from all future contact")
    ap.add_argument("--reason", default="opted out", help="reason, used with --suppress")
    ap.add_argument("--status", default="Sent", help="status to record, used with --batch/--email")
    args = ap.parse_args()

    if not (args.batch or args.email or args.suppress):
        ap.error("give one of --batch, --email, or --suppress")

    today = f"{date.today():%Y-%m-%d}"

    if args.suppress:
        rows = load(SUPPRESSION, SUPPRESSION_FIELDS)
        existing = {(r.get("email") or "").lower() for r in rows}
        added = 0
        for email in args.suppress:
            e = email.strip().lower()
            if e in existing:
                print(f"  already suppressed: {e}")
                continue
            rows.append({
                "email": e,
                "domain": domain_from_email(e),
                "company": "",
                "reason": args.reason,
                "date_added": today,
            })
            added += 1
            print(f"  suppressed: {e} ({args.reason})")
        save(SUPPRESSION, SUPPRESSION_FIELDS, rows)
        print(f"\n{added} suppressed. These will never be contacted again.")
        return

    master = load(MASTER, MASTER_FIELDS)
    seen = {(r.get("email") or "").lower() for r in master}
    new_rows = []

    if args.batch:
        if not os.path.exists(args.batch):
            sys.exit(f"Batch not found: {args.batch}")
        batch_id = os.path.splitext(os.path.basename(args.batch))[0]
        with open(args.batch, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                e = (r.get("email") or "").strip().lower()
                if not e or e in seen:
                    continue
                seen.add(e)
                new_rows.append({
                    "email": e,
                    "domain": registrable_domain(r.get("domain", "")),
                    "company": r.get("company", ""),
                    "first_name": r.get("first_name", ""),
                    "icp": r.get("icp", ""),
                    "batch_id": batch_id,
                    "date_first_contacted": today,
                    "status": args.status,
                })

    if args.email:
        for email in args.email:
            e = email.strip().lower()
            if e in seen:
                print(f"  already recorded: {e}")
                continue
            seen.add(e)
            new_rows.append({
                "email": e,
                "domain": domain_from_email(e),
                "company": "", "first_name": "", "icp": "", "batch_id": "manual",
                "date_first_contacted": today,
                "status": args.status,
            })

    master.extend(new_rows)
    save(MASTER, MASTER_FIELDS, master)
    for r in new_rows:
        print(f"  recorded: {r['email']} ({r['status']})")
    print(f"\n{len(new_rows)} recorded. Master list now holds {len(master)} contacted leads.")
    print("Future batches will dedup against these by email and domain.")


if __name__ == "__main__":
    main()
