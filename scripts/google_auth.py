#!/usr/bin/env python3
"""
Google OAuth for Gmail + Sheets.

Gmail and Sheets cannot be authorized with a plain API key — they need OAuth2 on
behalf of your account. One-time setup:

  1. console.cloud.google.com -> new project
  2. Enable "Gmail API" and "Google Sheets API"
  3. OAuth consent screen -> External -> add yourself as a test user
  4. Credentials -> Create OAuth client ID -> Desktop app -> download JSON
  5. Save it as  credentials.json  in the project root
  6. Run:  .venv/bin/python scripts/google_auth.py

Step 6 opens a browser once and writes token.json. Both files are gitignored.
Delete token.json to re-consent (needed if you change SCOPES).
"""

import os
import sys
import warnings

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CREDENTIALS = config.get("GOOGLE_CREDENTIALS_FILE", os.path.join(ROOT, "credentials.json"))
TOKEN = config.get("GOOGLE_TOKEN_FILE", os.path.join(ROOT, "token.json"))

# Least privilege that still does the job:
#   gmail.send     - send, but cannot read your inbox at large
#   gmail.readonly - needed only to detect replies so a sequence stops
#   spreadsheets   - read drafts/approvals, write back status
SCOPES = [
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

_services = {}


def _require_interactive():
    """Refuse to start the browser consent flow when nobody is there to complete it.

    run_local_server() opens a browser and then blocks until someone finishes the
    consent screen. Under launchd, cron, or send_approved.py --watch there is no
    browser and no one to click, so it blocks forever — a daily run that hangs holding
    its lock, or a watcher that looks alive while sending nothing. Failing loudly is
    strictly better than a process that waits indefinitely for a click.

    Set OUTREACH_ALLOW_BROWSER_AUTH=1 to force the flow anyway.
    """
    if config.get("OUTREACH_ALLOW_BROWSER_AUTH", "").strip() in ("1", "true", "yes"):
        return
    if sys.stdin is not None and sys.stdin.isatty():
        return
    raise SystemExit(
        "\nGoogle authorization is needed, but this is not an interactive session.\n"
        "Re-authorizing opens a browser and waits for a click, which would hang a\n"
        "scheduled or --watch run indefinitely.\n\n"
        "Run this from a terminal instead:\n"
        "    .venv/bin/python scripts/google_auth.py\n"
    )


def get_credentials():
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    creds = None
    if os.path.exists(TOKEN):
        creds = Credentials.from_authorized_user_file(TOKEN, SCOPES)

    if creds and creds.valid:
        return creds

    refreshed = False
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            refreshed = True
        except Exception as e:
            # A revoked or expired refresh token lands here. Falling through to the
            # browser flow is right at a terminal and catastrophic unattended — see
            # _require_interactive below.
            print(f"  token refresh failed: {str(e)[:120]}")
            creds = None

    if not refreshed:
        if not os.path.exists(CREDENTIALS):
            raise SystemExit(
                "\nMissing credentials.json.\n"
                "Follow the setup steps at the top of scripts/google_auth.py.\n"
                f"Expected at: {CREDENTIALS}\n"
            )
        _require_interactive()
        flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS, SCOPES)
        creds = flow.run_local_server(port=0)

    with open(TOKEN, "w") as f:
        f.write(creds.to_json())
    os.chmod(TOKEN, 0o600)
    return creds


def service(name, version):
    """Cached Google API client."""
    key = (name, version)
    if key not in _services:
        from googleapiclient.discovery import build
        _services[key] = build(name, version, credentials=get_credentials(), cache_discovery=False)
    return _services[key]


def gmail():
    return service("gmail", "v1")


def sheets():
    return service("sheets", "v4")


_address_cache = {}


def sending_address(attempts=4):
    """The address Gmail will actually send from — used for the compliance footer.

    Retried and then cached for the process. This is the first network call a send run
    makes, and it had no retry at all: a single dropped connection here killed the run
    before a single message was built, which for an unattended --watch loop means it
    silently stops sending. Everything downstream (Sheets, the send itself) already
    backs off; this was the one unguarded step.
    """
    import random
    import socket
    import time

    if "addr" in _address_cache:
        return _address_cache["addr"]

    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            addr = gmail().users().getProfile(userId="me").execute().get("emailAddress", "")
            _address_cache["addr"] = addr
            return addr
        except (socket.timeout, socket.error, TimeoutError, ConnectionError) as e:
            if attempt == attempts:
                raise
            wait = delay + random.uniform(0, 1)
            print(f"    gmail profile: {type(e).__name__}, retry {attempt}/{attempts - 1} in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2
        except Exception as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
            wait = delay + random.uniform(0, 1)
            print(f"    gmail profile: HTTP {status}, retry {attempt}/{attempts - 1} in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2


if __name__ == "__main__":
    creds = get_credentials()
    print(f"\nAuthorized. Token saved to {TOKEN}")
    try:
        print(f"Gmail account: {sending_address()}")
    except Exception as e:
        print(f"Could not reach Gmail: {e}")
    print("Scopes:")
    for s in SCOPES:
        print(f"  {s}")
    print()
