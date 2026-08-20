#!/usr/bin/env python3
"""
Pull leads from the Apollo API into Leads/Inbox/ as a CSV.

Writes Apollo's own export column names, so process_batch.py consumes the output
unchanged — whether the CSV came from here or from a manual UI export.

    .venv/bin/python scripts/apollo_pull.py --icp ecom --limit 25 --dry-run
    .venv/bin/python scripts/apollo_pull.py --icp ecom --limit 25

Two stages, because Apollo's API works that way now:

  1. mixed_people/api_search  — free, but returns only a teaser: first name, title,
     company name, an id, and booleans like has_email. Last names come back
     obfuscated ("Ro***g") and there is no address, domain, employee count or
     industry in the response at all.
  2. people/match             — one credit per lead, returns the real record:
     verified email, employee count, industry, technologies, domain, LinkedIn.

So enrichment is not optional any more. Without it every row lacks the fields
process_batch.py qualifies on, and the whole batch would be rejected. --limit is
therefore a spend control: it caps how many credits this run can consume.

Requires a paid Apollo plan, and an API key whose scope includes People Search and
People Enrichment (Settings -> Integrations -> API -> edit key).
"""

import argparse
import csv
import os
import sys
import time
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "Leads", "Inbox")
BASE = "https://api.apollo.io/api/v1"

# Filters mirror CLAUDE.md Sections 3 and 4. Change them there first, then here.
#
# ecom is filtered on the technology a company *runs*, not on keywords. Searching
# q_organization_keyword_tags for "shopify"/"ecommerce" returns Shopify agencies —
# "Praella - Shopify Platinum Partner", "Third and Grove - Shopify Platinum" — because
# those words are in their name and pitch. Those are ICP #2 at best, and sending them
# the ecom pitch (which namedrops client work) is exactly what CLAUDE.md §5 forbids.
# currently_using_any_of_technology_uids finds the brands with a real storefront.
ICP_FILTERS = {
    "ecom": {
        "person_titles": ["founder", "co-founder", "ceo", "owner", "head of ecommerce",
                          "head of growth", "marketing director", "cmo",
                          "email marketing manager", "head of retention"],
        "organization_num_employees_ranges": ["11,20", "21,50", "51,100", "101,200"],
        "currently_using_any_of_technology_uids": ["shopify", "klaviyo", "recharge"],
    },
    "agency": {
        "person_titles": ["founder", "co-founder", "owner", "managing director",
                          "head of client services", "account director", "partner"],
        "organization_num_employees_ranges": ["5,10", "11,20", "21,50"],
        "q_organization_keyword_tags": ["marketing agency", "digital agency",
                                        "advertising agency", "creative agency"],
    },
}

COUNTRIES = ["United States", "Canada", "United Kingdom", "Australia"]

# Apollo's export header names — process_batch.py matches on these.
COLUMNS = ["First Name", "Last Name", "Title", "Company", "Email", "Email Status",
           "Website", "Person Linkedin Url", "# Employees", "Annual Revenue", "Industry",
           "Technologies", "Country", "City"]


def post(path, payload, api_key, attempts=3):
    import random
    import requests

    delay = 3.0
    for attempt in range(1, attempts + 1):
        try:
            r = requests.post(
                f"{BASE}/{path}",
                headers={"X-Api-Key": api_key, "Content-Type": "application/json",
                         "Cache-Control": "no-cache"},
                json=payload, timeout=60,
            )
            # Transient server-side trouble: back off rather than abandoning the pull.
            if r.status_code in (500, 502, 503, 504) and attempt < attempts:
                raise requests.exceptions.ConnectionError(f"HTTP {r.status_code}")
            break
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if attempt == attempts:
                raise SystemExit(f"Apollo unreachable after {attempts} tries: {e}")
            wait = delay + random.uniform(0, 1)
            print(f"    apollo: {type(e).__name__}, retrying in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2
    if r.status_code == 401:
        raise SystemExit("Apollo rejected the API key (401). Check APOLLO_API_KEY in .env.")
    if r.status_code == 403:
        # Apollo gates search/match behind paid plans. Its own message names the plan,
        # so surface it rather than guessing, and point at the fallback that works today.
        try:
            detail = r.json().get("error", "")
        except Exception:
            detail = r.text[:200]
        raise SystemExit(
            f"\nApollo refused this endpoint (403).\n  {detail}\n\n"
            "Your API key is fine — this is a plan limit, not an auth problem.\n"
            "Either upgrade Apollo, or skip this script and export a CSV from the\n"
            "Apollo web UI into Leads/Inbox/. Everything downstream is identical:\n"
            "  .venv/bin/python scripts/process_batch.py Leads/Inbox/<file>.csv --icp ecom\n"
        )
    if r.status_code == 422:
        # Apollo retires endpoints this way; the body names the replacement.
        try:
            detail = r.json().get("error", "")
        except Exception:
            detail = r.text[:200]
        raise SystemExit(f"\nApollo rejected this request (422).\n  {detail}\n")
    if r.status_code == 429:
        raise SystemExit("Apollo rate limit hit (429). Wait and retry with a lower --limit.")
    if not r.ok:
        raise SystemExit(f"Apollo error {r.status_code}: {r.text[:300]}")
    return r.json()


def search(icp, page, per_page, api_key):
    """One page of teasers. Returns (people, total_entries).

    api_search has no pagination object — just total_entries — and consecutive pages
    can repeat the same person, so callers must dedupe on id.
    """
    payload = dict(ICP_FILTERS[icp])
    payload.update({"person_locations": COUNTRIES, "page": page, "per_page": per_page})
    data = post("mixed_people/api_search", payload, api_key)
    return data.get("people", []), data.get("total_entries", 0)


def enrich(person_id, api_key):
    """Turn a teaser into a real record. COSTS ONE CREDIT.

    Matching by Apollo's own person id rather than by name+domain: the search teaser
    obfuscates the last name ("Ro***g") and returns no domain at all, so a name-based
    match would be guessing at exactly the field it needs to be right about.
    """
    try:
        return post("people/match", {"id": person_id}, api_key).get("person") or {}
    except SystemExit:
        raise
    except Exception:
        return {}


def is_masked(email):
    return not email or "email_not_unlocked" in email.lower()


SOURCED = config.get("OUTREACH_SOURCED", os.path.join(ROOT, "Leads", "sourced-ledger.csv"))
SOURCED_FIELDS = ["company", "domain", "email", "icp", "date_sourced"]


def known_companies():
    """Company names we must not pay to enrich again, lowercased.

    Reads two ledgers, and it needs both:
      master-list.csv   companies actually contacted
      sourced-ledger.csv companies already pulled and paid for

    master-list is only written when a message really sends, so on its own it does
    not know about leads sitting unsent in the Outbox. For a one-off run that just
    looks untidy; for a daily scheduled run it means re-buying the same people every
    morning until they happen to be emailed.

    The teaser carries a company name and nothing else, so company name is the only
    dedup signal available before a credit is spent.
    """
    import csv
    names = set()
    paths = [config.get("OUTREACH_MASTER", os.path.join(ROOT, "Leads", "master-list.csv")),
             SOURCED]
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            for r in csv.DictReader(f):
                name = (r.get("company") or "").strip().lower()
                if name:
                    names.add(name)
    return names


def record_sourced(rows, icp):
    """Append everything we just paid to enrich, so tomorrow's run skips it."""
    import csv
    from datetime import date as _date
    existing = os.path.exists(SOURCED)
    os.makedirs(os.path.dirname(SOURCED), exist_ok=True)
    with open(SOURCED, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=SOURCED_FIELDS, extrasaction="ignore")
        if not existing:
            w.writeheader()
        for r in rows:
            w.writerow({"company": r.get("Company", ""),
                        "domain": r.get("Website", ""),
                        "email": r.get("Email", ""),
                        "icp": icp,
                        "date_sourced": f"{_date.today():%Y-%m-%d}"})


def to_row(person):
    org = person.get("organization") or {}
    tech = org.get("technology_names") or []
    return {
        "First Name": person.get("first_name") or "",
        "Last Name": person.get("last_name") or "",
        "Title": person.get("title") or "",
        "Company": org.get("name") or "",
        "Email": person.get("email") or "",
        # Apollo uses 'verified' / 'guessed' / 'unavailable'; process_batch only accepts verified.
        "Email Status": person.get("email_status") or "",
        "Website": org.get("website_url") or org.get("primary_domain") or "",
        "Person Linkedin Url": person.get("linkedin_url") or "",
        "# Employees": str(org.get("estimated_num_employees") or ""),
        # Enforces the $1-20M band in CLAUDE.md Section 3. Employee count alone lets
        # through brands far outside it — ONNIT is 140 staff and nine figures.
        "Annual Revenue": str(org.get("annual_revenue") or org.get("organization_revenue") or ""),
        "Industry": org.get("industry") or "",
        "Technologies": ", ".join(tech[:25]),
        "Country": person.get("country") or org.get("country") or "",
        "City": person.get("city") or org.get("city") or "",
    }


def collect_teasers(icp, api_key, wanted, per_page, max_pages=20, exclude=()):
    """Search until we have `wanted` distinct, enrichable leads or run out of pages.

    Deduped on id because api_search repeats people across pages, and filtered on
    has_email because enriching someone Apollo has no address for burns a credit to
    learn nothing.

    `exclude` (company names, lowercased) is applied here rather than after the
    caller truncates to `wanted` — filtering afterwards silently shrinks the batch,
    so asking for 30 leads returned 10 once 20 were already-sourced.
    """
    seen, companies, teasers, total = set(), set(), [], 0
    companies.update(exclude)
    for page in range(1, max_pages + 1):
        batch, total = search(icp, page, per_page, api_key)
        if not batch:
            break
        fresh = 0
        for p in batch:
            pid = p.get("id")
            if not pid or pid in seen:
                continue
            seen.add(pid)
            fresh += 1
            if not p.get("has_email"):
                continue
            # One person per company (CLAUDE.md §8). process_batch enforces this too,
            # but only after enrichment — by then the second credit is already spent.
            company = ((p.get("organization") or {}).get("name") or "").strip().lower()
            if company and company in companies:
                continue
            if company:
                companies.add(company)
            teasers.append(p)
        print(f"  page {page}: {len(batch)} returned, {fresh} new, "
              f"{len(teasers)} enrichable so far (Apollo reports {total} matching)")
        if len(teasers) >= wanted or fresh == 0:
            break
        time.sleep(1)  # be polite to the rate limiter
    return teasers[:wanted], total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icp", choices=["ecom", "agency"], required=True)
    ap.add_argument("--limit", type=int, default=25,
                    help="max leads to enrich — this is the credit spend for the run")
    ap.add_argument("--per-page", type=int, default=100)
    ap.add_argument("--dry-run", action="store_true",
                    help="search only: list who would be enriched, spend nothing")
    ap.add_argument("--out", help="output path (default: Leads/Inbox/<date>-<icp>-apollo.csv)")
    args = ap.parse_args()

    api_key = config.require("APOLLO_API_KEY", "pull leads from Apollo")

    # Cheapest possible dedup: never pay to enrich a company already in the ledger.
    contacted = known_companies()
    if contacted:
        print(f"  excluding {len(contacted)} already-contacted companies")

    print(f"\nSearching Apollo for {args.icp} leads...")
    teasers, total = collect_teasers(args.icp, api_key, args.limit, args.per_page,
                                     exclude=contacted)
    if not teasers:
        raise SystemExit("No enrichable leads returned. Loosen the filters in ICP_FILTERS.")
    if len(teasers) < args.limit:
        print(f"\n!  Only {len(teasers)} new leads available for these filters, "
              f"not the {args.limit} requested.")

    if args.dry_run:
        print(f"\nDRY RUN — would enrich {len(teasers)} leads ({len(teasers)} credits).\n")
        for t in teasers[:20]:
            org = (t.get("organization") or {}).get("name", "?")
            print(f"  {t.get('first_name','?'):12} {t.get('last_name_obfuscated','?'):10} "
                  f"{(t.get('title') or '')[:32]:34} {org[:34]}")
        if len(teasers) > 20:
            print(f"  ... and {len(teasers) - 20} more")
        print("\nNothing was enriched and no credits were spent.\n")
        return

    print(f"\nEnriching {len(teasers)} leads — this spends {len(teasers)} Apollo credits.")
    rows, failed = [], 0
    for i, t in enumerate(teasers, 1):
        person = enrich(t["id"], api_key)
        if not person or is_masked(person.get("email")):
            failed += 1
        else:
            rows.append(to_row(person))
        if i % 10 == 0 or i == len(teasers):
            print(f"    {i}/{len(teasers)} enriched, {len(rows)} with a usable email")
        time.sleep(0.3)

    if not rows:
        raise SystemExit("Every enrichment came back without an email. Nothing written.")

    os.makedirs(INBOX, exist_ok=True)
    out = args.out or os.path.join(INBOX, f"{date.today():%Y-%m-%d}-{args.icp}-apollo.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # Before anything else: record what we paid for, so a repeat run never re-buys it.
    record_sourced(rows, args.icp)

    print(f"\nWrote {len(rows)} leads -> {os.path.relpath(out, ROOT)}")
    if failed:
        print(f"   ({failed} enriched without a usable email and were dropped)")
    print(f"\nNext: .venv/bin/python scripts/process_batch.py "
          f"{os.path.relpath(out, ROOT)} --icp {args.icp}\n")


if __name__ == "__main__":
    main()
