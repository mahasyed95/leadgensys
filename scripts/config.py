#!/usr/bin/env python3
"""
Central config. Reads .env at the project root — no third-party dependency.

Everything has a working default, so the scripts run with no .env at all. Keys only
become necessary for the integrations: Apollo (API key) and Gmail/Sheets (OAuth, via
scripts/google_auth.py — not an API key).

Usage:
    from config import get, get_int, get_list, require
"""

import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(ROOT, ".env")

_cache = None


def _load():
    """Parse .env into a dict. Real environment variables win over the file."""
    global _cache
    if _cache is not None:
        return _cache
    values = {}
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                # Value is taken verbatim after '='. Inline comments are deliberately
                # NOT stripped: a postal address like "12 Main St #4" is a legitimate
                # value, and treating " #" as a comment silently truncated it.
                # Put comments on their own line.
                values[key.strip()] = val.strip().strip('"').strip("'")
    prefixes = ("APOLLO_", "GOOGLE_", "GMAIL_", "SEND_", "SENDER_", "REPLY_",
                "NEVERBOUNCE_", "ZEROBOUNCE_", "OUTREACH_", "ECOM_", "AGENCY_", "DIVERSIFY_")
    values.update({k: v for k, v in os.environ.items() if k in values or k.startswith(prefixes)})
    _cache = values
    return _cache


def get(key, default=None):
    val = _load().get(key)
    return val if val not in (None, "") else default


def get_int(key, default):
    try:
        return int(str(get(key, default)).strip())
    except (TypeError, ValueError):
        return default


def get_list(key, default):
    """Comma-separated list, lowercased and stripped."""
    raw = get(key)
    if not raw:
        return default
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def require(key, why):
    """Fetch a key that an integration genuinely cannot run without."""
    val = get(key)
    if not val:
        raise SystemExit(
            f"\nMissing {key} in .env — needed to {why}.\n"
            f"Copy .env.example to .env and fill it in.\n"
        )
    return val


def status():
    """Report which integrations are configured. Never prints secret values."""
    checks = [
        ("Apollo (lead sourcing)", "APOLLO_API_KEY"),
        ("Firecrawl (web sourcing)", "FIRECRAWL_API_KEY"),
        ("Google Sheet (CRM)", "GOOGLE_SHEET_ID"),
        ("NeverBounce (verification)", "NEVERBOUNCE_API_KEY"),
        ("ZeroBounce (verification)", "ZEROBOUNCE_API_KEY"),
    ]
    print(f"\n.env: {'found' if os.path.exists(ENV_PATH) else 'not present (using defaults)'}\n")
    for label, key in checks:
        print(f"  {'SET     ' if get(key) else 'missing '} {label:32} {key}")
    print("\nFilters in effect:")
    print(f"  ecom employees    {get_int('ECOM_MIN_EMPLOYEES', 10)}-{get_int('ECOM_MAX_EMPLOYEES', 200)}")
    print(f"  agency employees  {get_int('AGENCY_MIN_EMPLOYEES', 5)}-{get_int('AGENCY_MAX_EMPLOYEES', 50)}")
    print(f"  diversify cap     {get_int('DIVERSIFY_CAP_PCT', 25)}%  (batches of {get_int('DIVERSIFY_MIN_BATCH', 20)}+)")
    print(f"  gmail daily cap   {get_int('GMAIL_DAILY_CAP', 40)} sends")
    print(f"  gap between sends {get_int('SEND_GAP_MIN_SECONDS', 90)}-{get_int('SEND_GAP_MAX_SECONDS', 420)}s")
    print()

    # Gmail/Sheets use OAuth, not an API key — report the token instead.
    for label, path in (("OAuth client (credentials.json)", os.path.join(ROOT, "credentials.json")),
                        ("OAuth token (token.json)", os.path.join(ROOT, "token.json"))):
        print(f"  {'present ' if os.path.exists(path) else 'MISSING '} {label}")
    if not os.path.exists(os.path.join(ROOT, "token.json")):
        print("\n  -> run: .venv/bin/python scripts/google_auth.py")
    print()


if __name__ == "__main__":
    status()
