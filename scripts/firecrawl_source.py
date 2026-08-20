#!/usr/bin/env python3
"""
Source candidate companies from the public web with Firecrawl.

    .venv/bin/python scripts/firecrawl_source.py --icp ecom --limit 20
    .venv/bin/python scripts/firecrawl_source.py --icp agency --query "email marketing agency london"

Writes an Apollo-shaped CSV into Leads/Inbox/, so process_batch.py consumes it
unchanged regardless of whether the rows came from Apollo or from here.

WHAT THIS CAN AND CANNOT DO
---------------------------
It finds real companies, detects their tech stack from the live page (Shopify,
Klaviyo, Recharge, Gorgias...), and pulls whatever contact address is published on
the site. That tech detection is genuinely better than a database's, because it is
what the site is running right now.

What it cannot do is give you a named decision-maker's address. Sites publish role
inboxes — hello@, info@, support@ — which land in a support queue, not with the
founder. So emails from here are written with status `scraped` and process_batch.py
REJECTS them by default (CLAUDE.md Section 11: never import an unverified list).

The intended use is therefore: run this to build a pre-qualified target list, then
spend your scarce Apollo credits looking up named contacts at those specific
companies, rather than on Apollo's own broad search results.

Pass --allow-role-emails to process_batch.py only if you have decided that mailing
hello@ at a ten-person brand is acceptable for a given batch.
"""

import argparse
import csv
import os
import re
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INBOX = os.path.join(ROOT, "Leads", "Inbox")

# Firecrawl's free tier permits roughly 10 requests per minute.
RATE_PER_MIN = config.get_int("FIRECRAWL_RATE_PER_MIN", 8)

# Same column names Apollo exports use — see apollo_pull.py.
COLUMNS = ["First Name", "Last Name", "Title", "Company", "Email", "Email Status",
           "Website", "Person Linkedin Url", "# Employees", "Industry",
           "Technologies", "Country", "City"]

DEFAULT_QUERIES = {
    # Aimed at the $1-20M band in CLAUDE.md Section 3. Generic "best Shopify brand"
    # queries surface Glossier and Fenty — real brands, but far outside the ICP and
    # not reachable by cold email anyway.
    "ecom": [
        "small independent Shopify skincare brands to watch",
        "indie DTC supplement brands founded recently",
        "emerging Shopify coffee roasters direct to consumer",
        "small batch Shopify apparel brands independent",
        "up and coming wellness brands Shopify small business",
    ],
    "agency": [
        "small ecommerce marketing agencies boutique",
        "independent Shopify agency partners small team",
        "boutique DTC branding agencies",
        "small web design agencies for ecommerce brands",
    ],
}

# Live tech fingerprints. Keys are what we report; values are markers in page source.
TECH_MARKERS = {
    "Shopify": ["cdn.shopify.com", "myshopify.com", "shopify.theme", "x-shopify"],
    "Klaviyo": ["klaviyo.js", "static.klaviyo.com", "static-tracking.klaviyo"],
    "Recharge": ["rechargepayments.com", "recharge-theme"],
    "Gorgias": ["gorgias.chat", "gorgias.io"],
    "Yotpo": ["yotpo.com", "staticw2.yotpo"],
    "Postscript": ["postscript.io", "sdk.postscript"],
    "Attentive": ["attentivemobile.com", "cdn.attn.tv"],
    "WooCommerce": ["woocommerce", "wp-content/plugins/woocommerce"],
    "BigCommerce": ["bigcommerce.com", "cdn11.bigcommerce"],
}

# Addresses that are never worth contacting.
JUNK_EMAIL = re.compile(
    r"(noreply|no-reply|donotreply|example\.|sentry\.|wixpress|@2x|\.png|\.jpg|\.gif|"
    r"\.svg|\.webp|godaddy|sentry\.io|@sentry)", re.I)
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

TLD_COUNTRY = {
    ".co.uk": "United Kingdom", ".uk": "United Kingdom",
    ".com.au": "Australia", ".au": "Australia",
    ".ca": "Canada", ".ie": "Ireland", ".de": "Germany", ".fr": "France",
    ".nl": "Netherlands", ".es": "Spain", ".it": "Italy", ".se": "Sweden",
    ".nz": "New Zealand",
}

SKIP_DOMAINS = {
    # Directories, marketplaces and press — not prospects.
    "shopify.com", "amazon.com", "etsy.com", "ebay.com", "walmart.com",
    "wikipedia.org", "reddit.com", "youtube.com", "facebook.com", "instagram.com",
    "linkedin.com", "pinterest.com", "tiktok.com", "x.com", "twitter.com",
    "medium.com", "forbes.com", "clutch.co", "g2.com", "trustpilot.com",
    "producthunt.com", "crunchbase.com", "yelp.com", "glassdoor.com",
    # Content and SEO sites that rank for "best X brand" queries.
    "webinopoly.com", "commerce-ui.com", "selfnamed.com", "shopify.dev",
    "hubspot.com", "semrush.com", "ahrefs.com", "similarweb.com", "statista.com",
    "wordpress.org", "wix.com", "squarespace.com", "bigcommerce.com", "klaviyo.com",
    "gorgias.com", "yotpo.com", "recharge.com", "attentive.com", "postscript.io",
    "google.com", "apple.com", "microsoft.com", "paypal.com", "stripe.com",
    "mailchimp.com", "canva.com", "vimeo.com", "spotify.com", "shopifycdn.com",
}


_last_call = [0.0]


def throttled(fn, *a, **kw):
    """Call Firecrawl no faster than RATE_PER_MIN, retrying on 429.

    The free tier allows about 10 requests a minute. Without this the run burns
    through its candidates as rate-limit errors and reports 'nothing scraped'.
    """
    import random
    import time

    gap = 60.0 / max(1, RATE_PER_MIN)
    delay = 8.0
    for attempt in range(1, 4):
        wait = gap - (time.time() - _last_call[0])
        if wait > 0:
            time.sleep(wait)
        _last_call[0] = time.time()
        try:
            return fn(*a, **kw)
        except Exception as e:
            if "rate limit" not in str(e).lower() or attempt == 3:
                raise
            back = delay + random.uniform(0, 2)
            print(f"      rate limited, backing off {back:.0f}s")
            time.sleep(back)
            delay *= 2


def client():
    from firecrawl import Firecrawl
    return Firecrawl(api_key=config.require("FIRECRAWL_API_KEY", "search and scrape with Firecrawl"))


def registrable(url):
    """Reduce a URL to the company domain.

    Delegates to process_batch.domain_of so this matches exactly what dedup later
    compares against — a second implementation drifted immediately, leaving query
    strings attached and making 'brand.com?x=1' look like a different company.
    """
    from process_batch import domain_of
    return domain_of("", url or "")


def country_from_domain(domain):
    for tld, name in sorted(TLD_COUNTRY.items(), key=lambda kv: -len(kv[0])):
        if domain.endswith(tld):
            return name
    return ""


def detect_tech(blob):
    low = (blob or "").lower()
    return [name for name, markers in TECH_MARKERS.items() if any(m in low for m in markers)]


def plausible_email(e):
    """Reject strings that match the email pattern but are not addresses.

    Page source is full of these. Chrome's MHTML frame markers
    ('frame-<32 hex>@mhtml.blink') look exactly like an email to a regex, and one was
    written into a lead CSV as a company's contact address — it would have hard
    bounced, and bounces are what cost you the sending domain.
    """
    local, _, host = e.partition("@")
    if not host or "." not in host:
        return False
    tld = host.rsplit(".", 1)[-1]
    if not tld.isalpha() or not (2 <= len(tld) <= 24):
        return False
    if host.endswith((".blink", ".invalid", ".local", ".localhost", ".test", ".example")):
        return False
    if len(local) > 40 or len(e) > 90:
        return False
    # Content-addressed junk: long hex runs are hashes, not names.
    if re.search(r"[0-9a-f]{16,}", local, re.I):
        return False
    return True


def best_email(blob, domain):
    """Prefer an address on the company's own domain; ignore junk and asset filenames."""
    found = []
    for e in EMAIL_RE.findall(blob or ""):
        e = e.strip().strip(".").lower()
        if JUNK_EMAIL.search(e) or not plausible_email(e):
            continue
        found.append(e)
    on_domain = [e for e in found if e.endswith("@" + domain) or e.endswith("." + domain)]
    ranked = on_domain or found
    # A named-looking local part beats a role inbox if both are present.
    role = ("info", "hello", "contact", "support", "sales", "admin", "team", "help")
    named = [e for e in ranked if e.split("@")[0] not in role]
    return (named or ranked or [""])[0]


def page_text(doc):
    """Firecrawl returns objects or dicts depending on version — handle both."""
    parts = []
    for attr in ("html", "raw_html", "rawHtml", "markdown", "links"):
        val = getattr(doc, attr, None)
        if val is None and isinstance(doc, dict):
            val = doc.get(attr)
        if isinstance(val, str):
            parts.append(val)
        elif isinstance(val, (list, tuple)):
            parts.append(" ".join(str(v) for v in val))
    meta = getattr(doc, "metadata", None) or (doc.get("metadata") if isinstance(doc, dict) else None)
    return "\n".join(parts), (meta or {})


def meta_get(meta, *keys):
    for k in keys:
        v = meta.get(k) if isinstance(meta, dict) else getattr(meta, k, None)
        if v:
            return str(v)
    return ""


BRAND_SCHEMA = {
    "type": "object",
    "properties": {
        "brands": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "website": {"type": "string"},
                },
                "required": ["name"],
            },
        }
    },
    "required": ["brands"],
}


def extract_brands(fc, url, icp, verbose=False):
    """Pull the companies a roundup article is actually about.

    Harvesting every outbound link does not work: articles link to their own tooling,
    hosting, social and analytics, and ranking those by frequency floats utilities like
    whois.com to the top while each real brand appears only once. Asking for the list
    the article is *about* sidesteps that entirely.
    """
    what = ("consumer ecommerce brands / online shops"
            if icp == "ecom" else "marketing, advertising, design or web agencies")
    try:
        res = throttled(
            fc.extract,
            urls=[url],
            prompt=(f"List the {what} that this page features or reviews. "
                    "Give each company's own website URL, not a link to a marketplace, "
                    "review site, or the publisher of this article. "
                    "Skip software tools, platforms, and the article publisher itself."),
            schema=BRAND_SCHEMA,
        )
    except Exception as e:
        if verbose:
            print(f"      extract failed: {str(e)[:90]}")
        return []

    data = getattr(res, "data", None)
    if data is None and isinstance(res, dict):
        data = res.get("data")
    if hasattr(data, "get"):
        brands = data.get("brands") or []
    else:
        brands = getattr(data, "brands", None) or []

    seed_domain = registrable(url)
    out = []
    for b in brands:
        name = (b.get("name") if isinstance(b, dict) else getattr(b, "name", "")) or ""
        site = (b.get("website") if isinstance(b, dict) else getattr(b, "website", "")) or ""
        d = registrable(site)
        if not d or d == seed_domain or d in SKIP_DOMAINS:
            continue
        if any(d == sk or d.endswith("." + sk) for sk in SKIP_DOMAINS):
            continue
        out.append((d, name.strip()[:70]))
    return out


def scrape_company(fc, url, verbose=False):
    """Scrape a homepage, and the contact page if the homepage yields no address."""
    domain = registrable(url)
    try:
        doc = throttled(fc.scrape, f"https://{domain}", formats=["markdown", "html"],
                        only_main_content=False, timeout=45000)
    except Exception as e:
        if verbose:
            print(f"      scrape failed: {str(e)[:90]}")
        return None

    blob, meta = page_text(doc)
    tech = detect_tech(blob)
    email = best_email(blob, domain)

    if not email:
        for path in ("contact", "pages/contact", "contact-us", "pages/contact-us"):
            try:
                sub = throttled(fc.scrape, f"https://{domain}/{path}",
                                formats=["markdown", "html"],
                                only_main_content=False, timeout=30000)
            except Exception:
                continue
            sub_blob, _ = page_text(sub)
            email = best_email(sub_blob, domain)
            if email:
                break

    return {
        "domain": domain,
        "company": meta_get(meta, "ogSiteName", "og_site_name", "title").split("|")[0].split("–")[0].strip()[:70]
                   or domain.split(".")[0].title(),
        "description": meta_get(meta, "description", "ogDescription", "og_description")[:300],
        "email": email,
        "tech": tech,
        "country": country_from_domain(domain),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--icp", choices=["ecom", "agency"], required=True)
    ap.add_argument("--query", action="append",
                    help="search query (repeatable). Defaults to a set per ICP.")
    ap.add_argument("--limit", type=int, default=15, help="companies to scrape (1 credit each)")
    ap.add_argument("--per-query", type=int, default=8, help="search results per query")
    ap.add_argument("--seeds", type=int, default=10,
                    help="seed pages to harvest links from (1 credit each)")
    ap.add_argument("--require-ecom-tech", action="store_true",
                    help="ecom only: drop companies with no Shopify/Woo/BigCommerce detected")
    ap.add_argument("--out", help="output CSV path")
    args = ap.parse_args()

    fc = client()
    queries = args.query or DEFAULT_QUERIES[args.icp]

    # --- 1. Search for seed pages ---
    seed_urls = []
    for q in queries:
        try:
            res = throttled(fc.search, q, limit=args.per_query, sources=["web"])
        except Exception as e:
            print(f"  search failed for {q!r}: {str(e)[:120]}")
            continue
        web = getattr(res, "web", None) or (res.get("web") if isinstance(res, dict) else []) or []
        urls = [getattr(i, "url", None) or (i.get("url") if isinstance(i, dict) else "") for i in web]
        seed_urls.extend(u for u in urls if u)
        print(f"  {q!r} -> {len(urls)} seed pages")

    # --- 2. Ask each seed page which companies it is actually about ---
    print(f"\nExtracting brands from {min(len(seed_urls), args.seeds)} seed pages...")
    names = {}
    for url in seed_urls[:args.seeds]:
        found = extract_brands(fc, url, args.icp, verbose=True)
        for d, name in found:
            names.setdefault(d, name)
        print(f"    {registrable(url):34} -> {len(found)} companies")

    candidates = list(names)[:args.limit]
    if not candidates:
        raise SystemExit("No companies extracted. Try a different --query.")
    print(f"\n{len(names)} distinct companies found; taking {len(candidates)}")

    # --- 2. Scrape each for tech stack and contact address ---
    print(f"\nScraping {len(candidates)} sites...")
    rows, skipped = [], 0
    for i, domain in enumerate(candidates, 1):
        info = scrape_company(fc, domain, verbose=True)
        if not info:
            skipped += 1
            continue
        tech = info["tech"]
        if args.icp == "ecom" and args.require_ecom_tech and not (
                set(tech) & {"Shopify", "WooCommerce", "BigCommerce"}):
            print(f"  {i:>3}. {domain:34} no store platform detected — skipped")
            skipped += 1
            continue
        rows.append({
            "First Name": "", "Last Name": "", "Title": "",
            "Company": names.get(domain) or info["company"],
            "Email": info["email"],
            # Never claim these are verified — process_batch rejects this status by default.
            "Email Status": "scraped" if info["email"] else "",
            "Website": f"https://{domain}",
            "Person Linkedin Url": "",
            "# Employees": "",
            "Industry": "Retail" if args.icp == "ecom" else "Marketing & Advertising",
            "Technologies": ", ".join(tech),
            "Country": info["country"],
            "City": "",
        })
        print(f"  {i:>3}. {domain:34} {', '.join(tech) or 'no tech detected':38} {info['email'] or '(no email found)'}")

    if not rows:
        raise SystemExit("Nothing usable scraped.")

    os.makedirs(INBOX, exist_ok=True)
    out = args.out or os.path.join(INBOX, f"{date.today():%Y-%m-%d}-{args.icp}-firecrawl.csv")
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)

    # No employee count is available from a website, so process_batch's size band
    # cannot apply to these rows. Big brands will pass qualification — say so.
    print("\n!  These rows carry no employee count, so the ICP size filter "
          "(CLAUDE.md Section 3)")
    print("   does not apply to them. Check the list for brands far above the "
          "$1-20M band before drafting.")

    with_email = sum(1 for r in rows if r["Email"])
    print(f"\nWrote {len(rows)} companies -> {os.path.relpath(out, ROOT)}")
    print(f"  {with_email} have a published address, {len(rows) - with_email} have none, {skipped} skipped")
    print("\nThese are COMPANIES, not named contacts. Two ways forward:")
    print("  1. Look these specific domains up in Apollo to get named decision-makers")
    print("  2. Accept role inboxes for this batch:")
    print(f"       process_batch.py {os.path.relpath(out, ROOT)} --icp {args.icp} --allow-role-emails\n")


if __name__ == "__main__":
    main()
