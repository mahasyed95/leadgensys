#!/usr/bin/env python3
"""
Send approved messages from the Outbox tab via Gmail. Built to run on cron.

    .venv/bin/python scripts/send_approved.py --dry-run    # always try this first
    .venv/bin/python scripts/send_approved.py

Each Outbox row is one lead carrying three drafted versions of the same message.
You read them and type 1, 2 or 3 into the `approve` column. That number — and only
that number — is what sends. A blank approve cell sends nothing, forever.

A row is sent only when ALL of these hold:
  - approve holds exactly 1, 2 or 3 (you typed it; nothing else writes that column)
  - the chosen variant actually has a subject and a body
  - status is DRAFT or blank (SENT, CANCELLED, FAILED and HOLD are all excluded)
  - the address is not in the Suppression tab or Leads/suppression.csv
  - the lead has not replied on any channel we can see
  - today's send count is under the daily cap

Status is written back to the sheet immediately after each send, before the next one
starts — so an interrupted run can never re-send a message.
"""

import argparse
import base64
import os
import random
import re
import sys
import time
import warnings
from datetime import date, datetime
from email.mime.text import MIMEText
from email.utils import formataddr

warnings.filterwarnings("ignore", category=FutureWarning)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import google_auth  # noqa: E402
import record_sent  # noqa: E402
import sheets  # noqa: E402
from process_batch import FREEMAIL, registrable_domain  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_SUPPRESSION = os.path.join(ROOT, "Leads", "suppression.csv")

DAILY_CAP = config.get_int("GMAIL_DAILY_CAP", 40)
GAP_MIN = config.get_int("SEND_GAP_MIN_SECONDS", 90)
GAP_MAX = config.get_int("SEND_GAP_MAX_SECONDS", 420)
SENDER_NAME = config.get("SENDER_NAME", "")
SENDER_ADDRESS = config.get("SENDER_ADDRESS", "")
REPLY_WINDOW_DAYS = config.get_int("REPLY_LOOKBACK_DAYS", 45)


SENT_KEYS = os.path.join(ROOT, "Leads", ".sent-row-keys")


def load_sent_keys():
    """row_keys already delivered, recorded locally the instant Gmail accepted them.

    The Sheet is the system of record, but writing to it can fail after a message has
    already gone out — and a row left with its approve number intact would be re-armed
    again on the next cron run. This file is the backstop that makes that impossible.
    """
    if not os.path.exists(SENT_KEYS):
        return set()
    with open(SENT_KEYS, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def sent_key(row):
    """Stable identity for the local sent ledger.

    Falls back to the email address when row_key is missing. It matters because
    load_sent_keys() discards blank lines: a row with an empty row_key wrote a blank
    line, which was then dropped, so that row had no crash protection at all. If the
    Sheets write failed after delivery, the row stayed armed and went out a second
    time — precisely the case this ledger exists to prevent, silently disabled for
    any row not created by push_to_sheet.py.
    """
    return ((row.get("row_key") or "").strip()
            or (row.get("email") or "").strip().lower())


def mark_sent_key(row):
    """Append and flush to disk immediately — this must survive a crash mid-run."""
    key = sent_key(row)
    if not key:
        # Nothing identifies this row, so nothing can protect it. Say so loudly rather
        # than write a blank line that load_sent_keys() will silently discard.
        print("    WARNING: row has neither row_key nor email — cannot record it as "
              "sent. If the sheet write fails it may be re-sent.")
        return
    with open(SENT_KEYS, "a", encoding="utf-8") as f:
        f.write(f"{key}\n")
        f.flush()
        os.fsync(f.fileno())


def send_with_retry(gmail, body, attempts=3):
    """Send, retrying only transient failures.

    A dropped connection or a 503 should not permanently mark a reviewed message
    FAILED and force re-approval. Anything else (bad address, auth) fails straight
    through — retrying those just repeats the same error.
    """
    import random
    import socket

    delay = 3.0
    for attempt in range(1, attempts + 1):
        try:
            return gmail.users().messages().send(userId="me", body=body).execute()
        except (socket.timeout, socket.error, TimeoutError, ConnectionError) as e:
            if attempt == attempts:
                raise
            wait = delay + random.uniform(0, 1)
            print(f"              {type(e).__name__}, retrying in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2
        except Exception as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
            wait = delay + random.uniform(0, 1)
            print(f"              HTTP {status}, retrying in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2


def local_suppression():
    """The CSV ledger is checked alongside the sheet — an opt-out recorded in either
    place must block the send."""
    import csv
    emails, domains = set(), set()
    if not os.path.exists(LOCAL_SUPPRESSION):
        return emails, domains
    with open(LOCAL_SUPPRESSION, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            e = (r.get("email") or "").strip().lower()
            d = (r.get("domain") or "").strip().lower()
            if e:
                emails.add(e)
            if d:
                domains.add(registrable_domain(d))
    return emails, domains


# A real opt-out *instruction*, not a passing mention of the word. The distinction
# matters more here than almost anywhere: we sell email marketing, so our own copy
# talks about unsubscribe rates, opt-out flows and list hygiene as subject matter.
OPTOUT_INSTRUCTION = re.compile(
    r"(reply\s+(?:with\s+)?[\"\u2018\u2019\u201c\u201d\']?(?:unsubscribe|stop|no thanks)"
    r"|click\s+(?:here\s+)?to\s+unsubscribe"
    r"|(?:to\s+)?opt[\s-]?out[,;:]?\s+(?:reply|click|email|just)"
    r"|(?:i\'ll|i will|we\'ll|we will)\s+(?:take|remove)\s+you\s+off"
    r"|let me know and i\'ll stop)",
    re.I,
)


def compliance_footer(body, from_addr):
    """CLAUDE.md Section 11: every message identifies the sender, carries a physical
    postal address, and offers a real opt-out. Added here rather than trusted to the
    draft, so it cannot be forgotten.

    The identification block is appended UNCONDITIONALLY. An earlier version returned
    the body untouched whenever it contained the substring "unsubscribe" or "opt out",
    meaning to avoid a doubled opt-out line — but that also stripped the sender name,
    the company, and the postal address. For this sender that misfire was close to
    guaranteed: an email-marketing agency writes "your unsubscribe rate is climbing"
    as ordinary copy, and every such draft would have gone out with no postal address
    at all. CLAUDE.md Section 13 is explicit that this is illegal in the US, not untidy.

    Only the opt-out *sentence* is conditional, and only on a genuine instruction.
    """
    text = body.rstrip()
    signer = SENDER_NAME or from_addr
    company = config.get("SENDER_COMPANY")
    lines = [text, "", "--", f"{signer}{' | ' + company if company else ''}"]
    # CAN-SPAM requires a valid physical postal address in commercial email. CASL and
    # the Australian Spam Act expect sender identification too, so this is the floor
    # across every market we send into. run_once refuses to send at all when this is
    # unset or implausible, so reaching here without it means dry-run only.
    if SENDER_ADDRESS:
        lines.append(SENDER_ADDRESS)
    if not OPTOUT_INSTRUCTION.search(text):
        lines.append('Reply "unsubscribe" and I\'ll take you off this list.')
    return "\n".join(lines)


# Postal-code shapes for the markets we send into. A postcode is the single strongest
# evidence that an address is actually deliverable, and its absence is what separates
# "30 Riverhead Close, Wales" (a street and a country — a letter cannot arrive) from a
# real address.
POSTCODE_PATTERNS = (
    r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b",      # UK   CF11 1AA
    r"\b\d{5}(?:-\d{4})?\b",                          # US   94107 / 94107-1234
    r"\b[A-Z]\d[A-Z]\s*\d[A-Z]\d\b",                # CA   M5V 2T6
    r"\b\d{4}\b",                                      # AU/EU 2000
)


def address_looks_deliverable(addr):
    """(ok, reason) — is this a postal address a letter could actually reach?

    CAN-SPAM requires a *valid physical postal address*, not merely a non-empty string,
    so checking presence alone leaves the hole wide open. The address that shipped with
    this system was "30 Riverhead Close, Wales": non-empty, passes any truthiness test,
    and is undeliverable — no town, no postcode. Mail sent under it is as non-compliant
    as mail sent with no address at all.

    Set SENDER_ADDRESS_VERIFIED=1 in .env to override, for the genuinely postcode-less
    address this cannot recognise.
    """
    addr = (addr or "").strip()
    if not addr:
        return False, "SENDER_ADDRESS is not set"
    if config.get("SENDER_ADDRESS_VERIFIED", "").strip() in ("1", "true", "yes"):
        return True, ""
    if not any(ch.isdigit() for ch in addr):
        return False, "no street number or postcode in %r" % addr
    if not any(re.search(p, addr, re.I) for p in POSTCODE_PATTERNS):
        return False, "no recognisable postcode in %r" % addr
    if len(addr) < 12:
        return False, "too short to be a full address: %r" % addr
    return True, ""


def has_replied(gmail, email):
    """True if this person has sent us anything recently. Any reply stops the sequence
    (CLAUDE.md Section 6)."""
    try:
        res = gmail.users().messages().list(
            userId="me", q=f"from:{email} newer_than:{REPLY_WINDOW_DAYS}d", maxResults=1,
        ).execute()
        return bool(res.get("messages"))
    except Exception as e:
        # Fail closed: if we cannot confirm no reply, do not send.
        print(f"    reply check failed for {email} ({e}) — skipping to be safe")
        return True


LIVE_STATUSES = ("", sheets.DRAFT)


def chosen_message(row):
    """The (variant, subject, body) this row is cleared to send, or None.

    Returns None for an unapproved row *and* for a row whose approved variant is
    blank — approving draft 3 when only two were written must not put an empty
    email in front of a prospect.
    """
    v = sheets.approved_variant(row)
    if v is None:
        return None
    subject = (row.get("subject_%d" % v) or "").strip()
    body = (row.get("body_%d" % v) or "").strip()
    if not subject or not body:
        return None
    return v, subject, body


def eligible_rows(rows, today=None):
    """Rows cleared to send right now. Kept as its own function so the tests exercise
    the real gate rather than a reimplementation of it.

    `today` is accepted and ignored: there is no per-row schedule in the variant
    layout — pacing is the daily cap plus the randomised gap between sends.
    """
    due = []
    for r in rows:
        if (r.get("status") or "").strip().upper() not in LIVE_STATUSES:
            continue
        if chosen_message(r) is None:
            continue
        due.append(r)
    return sorted(due, key=lambda r: (r.get("email") or "").lower())


def rejected_rows(rows):
    """Live rows the reviewer marked Reject. They are cancelled, never sent."""
    return [r for r in rows
            if (r.get("status") or "").strip().upper() in LIVE_STATUSES
            and sheets.approve_action(r) == "reject"]


def approved_but_blank(rows):
    """Rows where a variant was picked but that variant has no copy in it. These are
    silently unsendable, so they get surfaced rather than just skipped."""
    out = []
    for r in rows:
        if (r.get("status") or "").strip().upper() not in LIVE_STATUSES:
            continue
        v = sheets.approved_variant(r)
        if v is not None and chosen_message(r) is None:
            out.append((r, v))
    return out


def set_lead_stage(email, stage):
    """Keep the Leads tab in step with what actually happened. Never fatal — this is
    CRM bookkeeping, and the message has already been delivered by the time it runs."""
    try:
        sheets.set_lead_status(email, stage)
    except Exception as e:
        print(f"    (Leads tab not updated for {email}: {str(e)[:100]})")


def write_status(row, updates):
    """Write cells for a row, re-resolving its position first. Returns False if the
    row vanished from the sheet (deleted mid-run) — the caller must not assume the
    status landed."""
    try:
        current = sheets.resolve_row(row.get("row_key", ""), row["_row"])
        if current is None:
            print(f"    WARNING: row for {row.get('row_key')} no longer in the sheet; "
                  f"status not recorded")
            return False
        if current != row["_row"]:
            print(f"    (sheet moved: row {row['_row']} -> {current})")
        sheets.update_cells(sheets.OUTBOX, [(current, col, val) for col, val in updates])
        return True
    except Exception as e:
        # Bookkeeping only. The message is already delivered and already in the local
        # sent ledger, so failing here must not abort the rest of the batch.
        print(f"    WARNING: could not write status for {row.get('row_key')}: {str(e)[:140]}")
        return False


def build_message(row, subject, body, from_addr):
    msg = MIMEText(compliance_footer(body, from_addr), "plain", "utf-8")
    msg["To"] = row["email"]
    msg["From"] = formataddr((SENDER_NAME, from_addr)) if SENDER_NAME else from_addr
    msg["Subject"] = subject
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


def rfc_message_id(gmail, message_id):
    try:
        meta = gmail.users().messages().get(
            userId="me", id=message_id, format="metadata", metadataHeaders=["Message-ID"],
        ).execute()
        for h in meta.get("payload", {}).get("headers", []):
            if h.get("name", "").lower() == "message-id":
                return h.get("value", "")
    except Exception:
        pass
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show what would send, send nothing")
    ap.add_argument("--limit", type=int, default=0, help="override the daily cap downward")
    ap.add_argument("--no-pace", action="store_true", help="skip the delay between sends (testing only)")
    ap.add_argument("--watch", nargs="?", type=int, const=60, default=0, metavar="SECONDS",
                    help="keep running and send each row as it gets approved "
                         "(default every 60s). Ctrl-C to stop.")
    args = ap.parse_args()

    if args.watch:
        print(f"Watching the Outbox every {args.watch}s. Approve a row and it goes out.")
        print("Ctrl-C to stop.\n")
        while True:
            try:
                # The header row is cached for the life of the process, but a watcher
                # lives for hours. If a column is dragged to a new position mid-run,
                # a stale cache computes every write from the old order — stamping
                # SENT over someone's message body. Re-read it each cycle.
                sheets.invalidate_header_cache()
                run_once(args)
            except SystemExit as e:
                # A refusal (e.g. no SENDER_ADDRESS) should stop the watcher outright
                # rather than repeat the same error every interval.
                print(e)
                return
            except Exception as e:
                # One bad cycle must not kill an unattended watcher.
                print(f"  cycle failed: {str(e)[:160]}")
            time.sleep(args.watch)
    run_once(args)


def run_once(args):
    rows = sheets.read(sheets.OUTBOX)
    if not rows:
        print("Outbox is empty.")
        return

    today = f"{date.today():%Y-%m-%d}"
    sent_today = sum(1 for r in rows if (r.get("sent_at") or "").startswith(today))
    cap = min(args.limit, DAILY_CAP) if args.limit else DAILY_CAP
    remaining = max(0, cap - sent_today)

    sup_emails, sup_domains = sheets.suppressed_emails()
    le, ld = local_suppression()
    sup_emails |= le
    sup_domains |= ld

    due = eligible_rows(rows)

    already = load_sent_keys()
    if already:
        blocked = [r for r in due if sent_key(r) in already]
        for r in blocked:
            print(f"  ALREADY SENT  {r.get('row_key')} — sheet still shows it armed; "
                  f"fixing status, not resending")
            if not args.dry_run:
                write_status(r, [("status", sheets.SENT),
                                 ("note", "recovered from local sent ledger")])
        due = [r for r in due if sent_key(r) not in already]

    print(f"\n=== send_approved {datetime.now():%Y-%m-%d %H:%M} ===")
    print(f"Approved and due: {len(due)}   Sent today: {sent_today}/{cap}   Slots left: {remaining}")

    # Reject is handled here rather than by a human editing status, so a reviewed-and-
    # declined lead cannot sit in the queue looking unreviewed.
    for row in rejected_rows(rows):
        print(f"  REJECTED    {row.get('email')} — cancelling, will never send")
        if not args.dry_run:
            write_status(row, [("status", sheets.CANCELLED), ("note", "rejected at review")])
            set_lead_stage(row.get("email", ""), "Not Interested")

    redrafts = [r for r in rows if sheets.approve_action(r) == "redraft"
                and (r.get("status") or "").strip().upper() in LIVE_STATUSES]
    if redrafts:
        print(f"  {len(redrafts)} row(s) marked Redraft — run scripts/list_redrafts.py")

    for row, v in approved_but_blank(rows):
        print(f"  !  {row.get('email')} has approve={v} but draft {v} is empty — "
              f"not sending. Fill it in or pick another variant.")

    if not due:
        print("Nothing to send.\n")
        return

    # CAN-SPAM requires a physical postal address in commercial email, and CASL/GDPR/
    # the Australian Spam Act all expect sender identification. Refusing outright
    # rather than warning: a warning printed by a background run is a warning nobody
    # reads, and the message would already be gone. LinkedIn drafting is unaffected.
    ok, why = address_looks_deliverable(SENDER_ADDRESS)
    if not ok and not args.dry_run:
        raise SystemExit(
            f"\nRefusing to send: {why}.\n"
            "CAN-SPAM requires a real, deliverable physical postal address in commercial\n"
            "email — a From address does not satisfy it, and neither does a street and a\n"
            "country with no town or postcode. Fix SENDER_ADDRESS in .env, then re-run.\n"
            "If the address genuinely has no postcode, set SENDER_ADDRESS_VERIFIED=1.\n"
            f"{len(due)} approved message(s) are waiting and will send once it is valid.\n"
        )
    if not remaining and not args.dry_run:
        print("Daily cap reached — stopping. Approved rows wait for tomorrow.\n")
        return

    gmail = None if args.dry_run else google_auth.gmail()
    from_addr = "(dry-run)" if args.dry_run else google_auth.sending_address()

    replied_cache, cancelled, sent, failed = {}, 0, 0, 0

    for row in due:
        if sent >= remaining and not args.dry_run:
            print(f"\nDaily cap reached after {sent} sends. The rest stay armed for tomorrow.")
            break

        email = (row.get("email") or "").strip().lower()
        domain = registrable_domain(email.split("@")[-1]) if "@" in email else ""
        variant, subject, body_text = chosen_message(row)

        if email in sup_emails or (domain and domain not in FREEMAIL and domain in sup_domains):
            print(f"  SUPPRESSED  {email} — cancelling")
            if not args.dry_run:
                write_status(row, [("status", sheets.CANCELLED),
                                   ("note", "suppressed at send time")])
                set_lead_stage(email, "Opted Out")
            cancelled += 1
            continue

        if email not in replied_cache:
            replied_cache[email] = False if args.dry_run else has_replied(gmail, email)
        if replied_cache[email]:
            print(f"  REPLIED     {email} — already in conversation, cancelling")
            if not args.dry_run:
                write_status(row, [("status", sheets.CANCELLED),
                                   ("note", "lead replied; not sending cold copy")])
                set_lead_stage(email, "Replied")
            cancelled += 1
            continue

        if args.dry_run:
            print(f"  WOULD SEND  v{variant}  {email:34} {subject[:44]}")
            sent += 1
            continue

        try:
            res = send_with_retry(gmail, build_message(row, subject, body_text, from_addr))
        except Exception as e:
            print(f"  FAILED      {email}: {str(e)[:120]}")
            write_status(row, [("status", sheets.FAILED), ("note", str(e)[:200])])
            failed += 1
            continue

        # First durable record that this message left. Must happen before the Sheets
        # write, because that write is the part that can fail after delivery.
        mark_sent_key(row)

        mid = rfc_message_id(gmail, res["id"])
        # Written before the next send so an interrupted run cannot double-send.
        # sent_variant records which of the three actually went out — without it the
        # sheet cannot tell you afterwards which angle earned a reply.
        wrote = write_status(row, [
            ("status", sheets.SENT),
            ("sent_variant", str(variant)),
            ("sent_at", f"{datetime.now():%Y-%m-%d %H:%M:%S}"),
            ("thread_id", res.get("threadId", "")),
            ("rfc_message_id", mid),
        ])
        if not wrote:
            print(f"    NOTE: {email} was delivered but the sheet was not updated. "
                  f"The local ledger will stop it resending.")

        # Close the dedup loop. The Sheet now knows this went out; master-list.csv is
        # what process_batch.py reads, so without this the next Apollo pull would
        # re-source and re-contact the same company.
        try:
            record_sent.record_send(
                email, domain, row.get("company", ""), row.get("first_name", ""),
                row.get("icp", ""), "outbox",
            )
        except Exception as e:
            print(f"    WARNING: sent, but master-list.csv not updated ({e}). "
                  f"Run: record_sent.py --email {email}")
        set_lead_stage(email, "Sent")
        sent += 1
        print(f"  SENT        v{variant}  {email:34} {subject[:44]}")

        if not args.no_pace and sent < remaining:
            gap = random.randint(GAP_MIN, GAP_MAX)
            print(f"              pausing {gap}s")
            time.sleep(gap)

    print(f"\nSent: {sent}   Cancelled: {cancelled}   Failed: {failed}")
    if args.dry_run:
        print("DRY RUN — nothing was sent and the sheet was not modified.")
    print()


if __name__ == "__main__":
    main()
