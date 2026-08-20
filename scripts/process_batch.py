#!/usr/bin/env python3
"""
Process an Apollo CSV export into a qualified, deduplicated, diversified lead batch.

Usage:
    python3 scripts/process_batch.py Leads/Inbox/<export>.csv [--icp ecom|agency] [--limit N]

Outputs:
    Leads/Batches/<batch-id>.csv    qualified leads, ready for enrichment + drafting
    Leads/Rejected/<batch-id>.csv   rejected leads with reasons (kept for audit + future filter tuning)
    prints a batch report (segment mix, diversification warnings, reject breakdown)

Nothing is sent. This only prepares leads for the drafting step.
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Overridable so tests (and dry runs) can point at throwaway ledgers instead of the
# live ones. Normal use never sets these.
MASTER = config.get("OUTREACH_MASTER", os.path.join(ROOT, "Leads", "master-list.csv"))
SUPPRESSION = config.get("OUTREACH_SUPPRESSION", os.path.join(ROOT, "Leads", "suppression.csv"))
OUTDIR = config.get("OUTREACH_OUTDIR", "")

ALLOWED_COUNTRIES = {
    # North America
    "united states", "usa", "us", "canada", "ca",
    # UK
    "united kingdom", "uk", "great britain", "england", "scotland", "wales", "northern ireland",
    # EU — GDPR applies; see CLAUDE.md Section 11
    "ireland", "germany", "france", "netherlands", "belgium", "spain", "portugal",
    "italy", "austria", "denmark", "sweden", "finland", "norway", "poland",
    "czech republic", "czechia", "greece", "romania", "hungary", "luxembourg",
    "slovakia", "slovenia", "croatia", "bulgaria", "estonia", "latvia", "lithuania",
    "cyprus", "malta", "switzerland",
    # APAC
    "australia", "au", "new zealand",
}
ALLOWED_COUNTRIES = config.get_list("ALLOWED_COUNTRIES", ALLOWED_COUNTRIES)

ECOM_EMPLOYEES = (config.get_int("ECOM_MIN_EMPLOYEES", 10), config.get_int("ECOM_MAX_EMPLOYEES", 200))
AGENCY_EMPLOYEES = (config.get_int("AGENCY_MIN_EMPLOYEES", 5), config.get_int("AGENCY_MAX_EMPLOYEES", 50))
DIVERSIFY_CAP_PCT = config.get_int("DIVERSIFY_CAP_PCT", 25)
DIVERSIFY_MIN_BATCH = config.get_int("DIVERSIFY_MIN_BATCH", 20)

# Titles with budget authority. Junior/coordinator titles are filtered out.
SENIORITY = [
    (100, r"\b(founder|co-?founder|owner|ceo|chief executive)\b"),
    (90, r"\b(president|managing director|partner)\b"),
    (80, r"\b(cmo|chief marketing)\b"),
    (70, r"\b(vp|vice president)\b.*\b(marketing|growth|ecommerce|e-commerce)\b"),
    (60, r"\bhead of\b.*\b(marketing|growth|ecommerce|e-commerce|client services|retention)\b"),
    (50, r"\b(marketing|growth|ecommerce|e-commerce|retention|crm|email)\s+(director|manager|lead)\b"),
    (45, r"\b(account director)\b"),
    (40, r"\bdirector\b.*\b(marketing|growth|ecommerce|e-commerce)\b"),
]
JUNIOR = r"\b(intern|assistant|coordinator|associate|junior|jr\.?|specialist\s+i\b|analyst)\b"

ECOM_TECH = {"shopify", "klaviyo", "recharge", "shopify plus", "gorgias", "yotpo", "postscript", "attentive"}
ECOM_INDUSTRY = {"retail", "consumer goods", "apparel", "fashion", "health", "wellness",
                 "cosmetics", "food", "beverage", "sporting goods", "furniture", "luxury goods"}
AGENCY_INDUSTRY = {"marketing", "advertising", "design", "public relations"}
AGENCY_NAME_HINT = r"\b(agency|agencies|media|marketing|creative|studio|digital|collective|labs?)\b"

# Industries with no consumer checkout. CLAUDE.md Section 3 disqualifies B2B-only
# companies, but an industry label alone used to be enough to pass: Yuna Health, an
# AI mental-health platform selling to employers via "Contact Sales", qualified as
# ecom and was only caught by reading the site by hand.
B2B_INDUSTRY = {"information technology & services", "computer software", "internet",
                "financial services", "banking", "insurance", "staffing & recruiting",
                "management consulting", "hospital & health care", "medical practice",
                "mental health care", "health care", "pharmaceuticals", "biotechnology",
                "nonprofit organization management", "civic & social organization",
                "real estate", "telecommunications", "computer & network security",
                "human resources", "legal services", "education management",
                "mechanical or industrial engineering", "logistics & supply chain",
                "wholesale", "import & export"}

# Revenue band from CLAUDE.md Section 3. Apollo returns organization.annual_revenue on
# the enrichment call, so this is enforceable rather than aspirational — ONNIT passes
# the 10-200 employee band on 140 staff but is far outside the revenue band.
ECOM_REVENUE = (config.get_int("ECOM_MIN_REVENUE", 1_000_000),
                config.get_int("ECOM_MAX_REVENUE", 20_000_000))

_mx_cache = {}


def _dig(args, timeout):
    import subprocess
    try:
        r = subprocess.run(["dig"] + args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except Exception:
        return None


def has_mx(domain, timeout=5):
    """True if mail to this domain can plausibly be delivered.

    A domain that genuinely cannot receive mail hard-bounces every send, and bounce
    rate above ~3% is what costs a sending domain (CLAUDE.md Section 11).

    The subtlety is telling "no MX" apart from "could not ask". `dig +short` prints
    nothing in both cases and still exits 0, so an earlier version of this read a
    SERVFAIL as an absent record and wrongly condemned two live domains
    (urbani.com, masterofmalt.com). We now read the response status: only NOERROR is
    a trustworthy answer, and everything else fails open.

    Absent MX is also not proof on its own — RFC 5321 falls back to the A record — so
    a domain with no MX but a working A record is still treated as deliverable.
    """
    domain = norm(domain)
    if not domain:
        return False
    if domain in _mx_cache:
        return _mx_cache[domain]

    ok = True  # default for every "we could not tell" path
    out = _dig(["MX", domain, "+time=%d" % max(1, timeout // 2)], timeout)
    if out is not None:
        if re.search(r"^\S+\s+\d+\s+IN\s+MX\s", out, re.M):
            ok = True
        elif "status: NXDOMAIN" in out:
            # The domain does not exist. The most confident "no" DNS can give.
            ok = False
        elif "status: NOERROR" in out:
            # Real "no MX" answer. RFC 5321 falls back to the A record, so check it
            # before condemning the domain.
            a = _dig(["+short", "A", domain], timeout)
            ok = bool(a and a.strip())
        # else: SERVFAIL / REFUSED / timeout -> leave ok True
    _mx_cache[domain] = ok
    return ok


def parse_revenue(raw):
    """Apollo writes annual revenue as a bare float ('10000000.0'); the UI export can
    write '10M' or '$10,000,000'. Returns 0 when unknown, which means 'do not judge'."""
    s = (raw or "").strip().lower().replace("$", "").replace(",", "")
    if not s:
        return 0
    mult = 1
    if s.endswith("k"):
        mult, s = 1_000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    elif s.endswith("b"):
        mult, s = 1_000_000_000, s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0

# Free/personal mail domains — a lead with one of these has no company domain to dedup on.
FREEMAIL = {"gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
            "icloud.com", "protonmail.com", "live.com", "msn.com"}


def norm(s):
    return (s or "").strip().lower()


def _key(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def pick(row, *names):
    """Fetch a column by any of its known names.

    Apollo's headers drift in spacing and punctuation between exports ('# Employees'
    vs 'Employees'), so we compare on alphanumerics only. We deliberately do NOT
    substring-match: 'Email' appears inside 'Email Status', and 'Company' inside
    'Company Phone', so a substring fallback silently returns the wrong column.
    Add an explicit alias to the call site instead.
    """
    keys = {_key(k): v for k, v in row.items() if k}
    for n in names:
        v = keys.get(_key(n))
        if v is not None:
            return (v or "").strip()
    return ""


# Multi-part public suffixes in our target markets. Needed so 'brand.co.uk' collapses
# to itself rather than to 'co.uk' — which would merge every UK company into one.
MULTI_TLD = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk", "plc.uk", "net.uk",
    "com.au", "net.au", "org.au", "edu.au", "gov.au", "id.au",
    "co.nz", "net.nz", "org.nz", "co.za", "com.br", "co.jp", "com.mx", "co.in",
}


def registrable_domain(host):
    """Reduce a hostname to the company-identifying domain (eTLD+1).

    'shop.brand.com' and 'brand.com' are the same company — without this they read as
    two separate leads and the company gets two sequences, which is exactly the
    spam signal CLAUDE.md Section 8 exists to prevent.
    """
    parts = [p for p in (host or "").split(".") if p]
    if len(parts) < 2:
        return host or ""
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_TLD:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def domain_of(email, website):
    host = ""
    if website:
        d = re.sub(r"^https?://", "", website.strip().lower())
        d = d.split("/")[0].split("?")[0].strip()
        if d.startswith("www."):
            d = d[4:]
        if "." in d:
            host = d
    if not host and email and "@" in email:
        d = email.split("@")[-1].strip().lower()
        if d not in FREEMAIL:
            host = d
    return registrable_domain(host) if host else ""


def seniority_score(title):
    t = norm(title)
    if not t:
        return 0
    if re.search(JUNIOR, t):
        return 0
    for score, pat in SENIORITY:
        if re.search(pat, t):
            return score
    return 0


def parse_employees(raw):
    digits = re.sub(r"[^\d]", "", (raw or "").split("-")[0])
    return int(digits) if digits else 0


def diversity_axis(icp):
    """Which field the 25% cap counts on, per ICP.

    Industry is the right axis for ecom, where it genuinely varies (apparel vs
    supplements vs coffee) and is exactly the "don't let supplements dominate a pull"
    case CLAUDE.md Section 8 describes.

    It is the *wrong* axis for agencies, where 'marketing & advertising' is not a niche
    that crept in — it is the ICP definition. Counting it would reject three quarters of
    a perfectly good agency batch for being made of agencies. So ICP #2 diversifies on
    company-size band instead, which is the other axis Section 8 asks for ("mix
    company-size bands within each batch") and does vary.
    """
    return "size band" if icp == "agency" else "industry"


# Bands follow the ICP #2 Apollo filter (5-50 staff), split where the buying behaviour
# changes: a 6-person shop has no retainer clients worth white-labelling for, a 40-person
# one has an account team.
_SIZE_BANDS = ((0, 9, "1-9"), (10, 19, "10-19"), (20, 49, "20-49"), (50, 10 ** 9, "50+"))


def size_band(employees):
    n = employees or 0
    if n <= 0:
        # Not the same as "tiny". Bucketing missing headcount into 1-9 would make an
        # enrichment gap look like a real size band and skew the mix silently.
        return "unknown"
    for lo, hi, label in _SIZE_BANDS:
        if lo <= n <= hi:
            return label
    return "unknown"


def diversity_key(lead, icp):
    if diversity_axis(icp) == "size band":
        return size_band(lead.get("employees"))
    return norm(lead.get("industry")) or "unknown"


def classify_icp(industry, technologies, company, website):
    """Return 'ecom', 'agency', or '' if neither is a clean match.

    Industry is checked before tech stack, and this order matters: agencies run
    Shopify and Klaviyo *on behalf of their clients*, so a tech-stack match must
    never override an agency industry. Getting this backwards sends an agency the
    ecom pitch — which namedrops client work, the one thing the agency sequence
    forbids (CLAUDE.md Section 5).
    """
    ind, tech, comp = norm(industry), norm(technologies), norm(company)

    if any(a in ind for a in AGENCY_INDUSTRY):
        return "agency"
    if any(i in ind for i in ECOM_INDUSTRY) or any(t in tech for t in ECOM_TECH):
        return "ecom"
    # No usable industry label — fall back to the company name. Weakest signal, so
    # it only gets a vote when nothing better exists.
    if not ind and re.search(AGENCY_NAME_HINT, comp):
        return "agency"
    return ""


def load_seen():
    """Emails and domains already contacted, in pipeline, or suppressed."""
    emails, domains = set(), set()
    for path in (MASTER, SUPPRESSION):
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                e = norm(pick(row, "email"))
                # Normalize the stored domain the same way new leads are, otherwise a
                # ledger entry of 'shop.brand.com' won't match an incoming 'brand.com'.
                d = registrable_domain(norm(pick(row, "domain")))
                if e:
                    emails.add(e)
                    # An email's own domain also blocks the company, even if the
                    # ledger row has no domain column filled in.
                    host = e.split("@")[-1]
                    if host and host not in FREEMAIL:
                        domains.add(registrable_domain(host))
                if d:
                    domains.add(d)
    return emails, domains


ACCEPTED_EMAIL_STATUS = ("verified", "valid", "likely to engage")


def qualify(lead, icp_filter, allow_role_emails=False, check_mx=True):
    """Return a rejection reason, or None if the lead passes.

    allow_role_emails relaxes two checks for company-level rows produced by
    firecrawl_source.py: the address is a published role inbox rather than a verified
    personal one, and there is no named person so no title to score. It is opt-in
    because both are real quality reductions — see CLAUDE.md Section 11.
    """
    if not lead["email"]:
        return "no email"
    accepted = ACCEPTED_EMAIL_STATUS + (("scraped",) if allow_role_emails else ())
    if lead["email_status"] and lead["email_status"] not in accepted:
        return f"email status: {lead['email_status']}"
    if lead["country"] and norm(lead["country"]) not in ALLOWED_COUNTRIES:
        return f"country out of scope: {lead['country']}"
    if not lead["domain"]:
        return "no company domain"
    if lead["seniority"] == 0 and not (allow_role_emails and not lead["title"]):
        return f"title lacks budget authority: {lead['title'] or '(blank)'}"
    if not lead["icp"]:
        return "no clean ICP match"
    if icp_filter and lead["icp"] != icp_filter:
        return f"ICP mismatch (is {lead['icp']}, want {icp_filter})"

    emp = lead["employees"]
    lo, hi = ECOM_EMPLOYEES if lead["icp"] == "ecom" else AGENCY_EMPLOYEES
    if emp and not (lo <= emp <= hi):
        return f"employee count {emp} outside {lead['icp']} range {lo}-{hi}"

    if lead["icp"] == "ecom":
        # A real store can carry a vague industry label, so the tech stack gets a veto
        # here — but a B2B industry with no ecom technology behind it is not a store.
        # .get() throughout: callers build lead dicts with different key sets
        # (firecrawl_source rows have no industry at all).
        ind = norm(lead.get("industry"))
        if ind in B2B_INDUSTRY and not any(t in norm(lead.get("technologies")) for t in ECOM_TECH):
            return f"B2B/no consumer checkout: {lead.get('industry')}"

        rev = lead.get("revenue") or 0
        rlo, rhi = ECOM_REVENUE
        if rev and not (rlo <= rev <= rhi):
            return f"revenue {rev / 1_000_000:.0f}M outside ecom range {rlo // 1_000_000}-{rhi // 1_000_000}M"

    # Last, because it is the only check that costs a network round-trip.
    if check_mx and not has_mx(lead.get("domain")):
        return f"no MX record on {lead.get('domain')} - would bounce"
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv_paths", nargs="+", metavar="CSV",
                    help="one or more CSVs, or a directory (e.g. Leads/Inbox)")
    ap.add_argument("--icp", choices=["ecom", "agency"], help="restrict batch to one ICP")
    ap.add_argument("--limit", type=int, default=0, help="cap batch size after qualification")
    ap.add_argument("--allow-role-emails", action="store_true",
                    help="accept company-level rows from firecrawl_source.py: published "
                         "role inboxes (hello@/info@) and no named contact. Lower quality "
                         "than a verified named decision-maker — opt in per batch.")
    ap.add_argument("--skip-mx", action="store_true",
                    help="skip the MX lookup (use offline). Leaves dead domains in the "
                         "batch, which bounce and damage sender reputation.")
    args = ap.parse_args()

    # Apollo's free tier caps an export at 25 records, so a month's sourcing arrives as
    # several small files. They must be merged BEFORE dedup runs, otherwise the same
    # company appearing in two exports produces two sequences.
    paths = []
    for given in args.csv_paths:
        if os.path.isdir(given):
            paths.extend(sorted(
                os.path.join(given, f) for f in os.listdir(given) if f.lower().endswith(".csv")))
        elif os.path.exists(given):
            paths.append(given)
        else:
            sys.exit(f"Not found: {given}")
    if not paths:
        sys.exit(f"No CSV files found in: {', '.join(args.csv_paths)}")

    seen_emails, seen_domains = load_seen()
    batch_id = f"{date.today():%Y-%m-%d}-{args.icp or 'mixed'}"

    qualified, rejected = [], []
    batch_domains = {}  # domain -> best lead, enforces one contact per company

    source_rows = []
    for path in paths:
        with open(path, newline="", encoding="utf-8-sig") as f:
            rows_here = list(csv.DictReader(f))
        source_rows.extend(rows_here)
        print(f"  read {len(rows_here):>4} rows from {os.path.basename(path)}")

    if len(paths) > 1:
        print(f"  merged {len(source_rows)} rows from {len(paths)} files")

    for row in source_rows:
        email = norm(pick(row, "Email"))
        website = pick(row, "Website", "Company Website")
        company = pick(row, "Company", "Company Name for Emails", "Account Name")
        title = pick(row, "Title", "Job Title")
        lead = {
            "first_name": pick(row, "First Name"),
            "last_name": pick(row, "Last Name"),
            "title": title,
            "company": company,
            "email": email,
            "email_status": norm(pick(row, "Email Status")),
            "domain": domain_of(email, website),
            "website": website,
            "linkedin": pick(row, "Person Linkedin Url", "Linkedin Url"),
            "employees": parse_employees(pick(row, "# Employees", "Employees", "Company Size")),
            "revenue": parse_revenue(pick(row, "Annual Revenue", "Organization Revenue", "Revenue")),
            "industry": pick(row, "Industry"),
            "technologies": pick(row, "Technologies", "Keywords"),
            "country": pick(row, "Country", "Company Country"),
            "city": pick(row, "City", "Company City"),
            "seniority": seniority_score(title),
        }
        lead["icp"] = classify_icp(lead["industry"], lead["technologies"], company, website)
        # LinkedIn is the primary channel (CLAUDE.md Section 6), but a lead without a
        # profile is still worth emailing — it is marked, not dropped.
        lead["channel"] = "linkedin+email" if lead["linkedin"] else "email-only"

        # Dedup runs before qualification so the duplicate rate reported here is
        # the true one. If a lead is both a duplicate and unqualified, "duplicate"
        # is the fact worth tracking (CLAUDE.md Section 14).
        if lead["email"] in seen_emails:
            rejected.append({**lead, "reject_reason": "duplicate: email already in master/suppression"})
            continue
        if lead["domain"] and lead["domain"] in seen_domains:
            rejected.append({**lead, "reject_reason": f"duplicate: domain {lead['domain']} already contacted"})
            continue

        reason = qualify(lead, args.icp, args.allow_role_emails, check_mx=not args.skip_mx)
        if reason:
            rejected.append({**lead, "reject_reason": reason})
            continue

        # One contact per company within this batch — keep the most senior.
        existing = batch_domains.get(lead["domain"])
        if existing:
            loser = lead if lead["seniority"] <= existing["seniority"] else existing
            winner = existing if loser is lead else lead
            rejected.append({**loser, "reject_reason": f"duplicate domain in batch (kept {winner['title']})"})
            batch_domains[lead["domain"]] = winner
            continue
        batch_domains[lead["domain"]] = lead

    qualified = sorted(batch_domains.values(), key=lambda x: -x["seniority"])

    # --- Diversification: cap any single sub-segment at 25% of the batch ---
    # Only enforced on batches large enough for the cap to be meaningful. On a small
    # batch, 25% is a handful of leads and trimming would throw away good prospects,
    # so we warn about a skewed mix instead of cutting it.
    warnings = []
    # Keyed off each lead's own ICP, not the --icp flag: the flag is optional, so a
    # mixed batch must still count an agency on size band and an ecom brand on industry.
    dominant_icp = (Counter(l["icp"] for l in qualified).most_common(1)[0][0]
                    if qualified else args.icp)
    axis = diversity_axis(dominant_icp)
    if len(qualified) >= DIVERSIFY_MIN_BATCH:
        cap = max(1, int(len(qualified) * DIVERSIFY_CAP_PCT / 100))
        kept, per_segment = [], Counter()
        for lead in qualified:
            key = diversity_key(lead, lead["icp"])
            if per_segment[key] >= cap:
                rejected.append({**lead, "reject_reason": f"diversification cap: '{key}' already at {cap} ({DIVERSIFY_CAP_PCT}%)"})
                continue
            per_segment[key] += 1
            kept.append(lead)
        if len(kept) < len(qualified):
            warnings.append(f"trimmed {len(qualified) - len(kept)} leads to hold the {DIVERSIFY_CAP_PCT}% per-{axis} cap")
        qualified = kept
    elif len(qualified) >= 5:
        mix = Counter(diversity_key(l, l["icp"]) for l in qualified)
        top, n = mix.most_common(1)[0]
        if n / len(qualified) > DIVERSIFY_CAP_PCT / 100:
            warnings.append(
                f"batch too small ({len(qualified)}) to enforce the {DIVERSIFY_CAP_PCT}% cap, but {axis} '{top}' is "
                f"{n}/{len(qualified)} of it — vary the mix on your next Apollo pull"
            )

    if args.limit and len(qualified) > args.limit:
        # Take leads round-robin across industries rather than straight off the top of
        # the seniority sort — otherwise the limit re-skews the mix diversification
        # just balanced.
        buckets = {}
        for lead in qualified:
            buckets.setdefault(norm(lead["industry"]) or "unknown", []).append(lead)
        picked = []
        while len(picked) < args.limit and any(buckets.values()):
            for key in list(buckets):
                if buckets[key] and len(picked) < args.limit:
                    picked.append(buckets[key].pop(0))
        picked_ids = {id(l) for l in picked}
        for lead in qualified:
            if id(lead) not in picked_ids:
                rejected.append({**lead, "reject_reason": "over batch limit"})
        qualified = sorted(picked, key=lambda x: -x["seniority"])

    # --- Write outputs ---
    q_dir = OUTDIR or os.path.join(ROOT, "Leads", "Batches")
    r_dir = OUTDIR or os.path.join(ROOT, "Leads", "Rejected")
    os.makedirs(q_dir, exist_ok=True)
    os.makedirs(r_dir, exist_ok=True)
    out_q = os.path.join(q_dir, f"{batch_id}.csv")
    out_r = os.path.join(r_dir, f"{batch_id}-rejected.csv" if OUTDIR else f"{batch_id}.csv")

    fields = ["first_name", "last_name", "title", "company", "email", "domain", "website",
              "linkedin", "channel", "employees", "revenue", "industry", "technologies",
              "country", "city", "icp", "seniority"]
    for path, rows, extra in ((out_q, qualified, []), (out_r, rejected, ["reject_reason"])):
        if not rows:
            continue
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields + extra, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)

    # --- Report ---
    if args.allow_role_emails and qualified:
        anon = sum(1 for l in qualified if not l["first_name"])
        if anon:
            print(f"\n!  {anon} of {len(qualified)} rows have no named contact. Drafts for these")
            print("   must not open with a first name — they go to a role inbox.")

    total = len(qualified) + len(rejected)
    dupes = sum(1 for r in rejected if r["reject_reason"].startswith("duplicate"))
    print(f"\n=== Batch {batch_id} ===")
    print(f"Qualified: {len(qualified)}   Rejected: {len(rejected)}   Sourced: {total}")
    if total:
        # Tracked per CLAUDE.md Section 14 — should trend down as list hygiene improves.
        print(f"Duplicate rate: {dupes}/{total} ({dupes / total:.0%})")
    if qualified:
        print(f"\nICP mix:      {dict(Counter(l['icp'] for l in qualified))}")
        print(f"Country mix:  {dict(Counter(l['country'] or '?' for l in qualified))}")
        print("Top industries:")
        for ind, n in Counter(l["industry"] or "unknown" for l in qualified).most_common(6):
            print(f"   {n:>4}  {ind}")
    if rejected:
        print("\nReject reasons:")
        for reason, n in Counter(r["reject_reason"].split(":")[0] for r in rejected).most_common():
            print(f"   {n:>4}  {reason}")
    for warn in warnings:
        print(f"\n!  {warn}")
    def show(path):
        rel = os.path.relpath(path, ROOT)
        return path if rel.startswith("..") else rel

    print(f"\nWrote: {show(out_q) if qualified else '(no qualified leads)'}")
    if rejected:
        print(f"Wrote: {show(out_r)}")
    print("\nNext: draft each qualified lead, then push_to_sheet.py for approval.")
    print("Leads are NOT added to master-list.csv until their messages actually send.\n")


if __name__ == "__main__":
    main()
