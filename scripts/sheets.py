#!/usr/bin/env python3
"""
Google Sheets as the CRM. Replaces GoHighLevel.

Three tabs, created automatically on first use:

  Leads        every lead ever qualified — the CRM record
  Outbox       drafted messages awaiting approval; this is where you approve
  Suppression  opt-outs and bounces; checked again at send time

Set up:
    .venv/bin/python scripts/sheets.py --init

That creates the tabs and prints the URL. Put the spreadsheet ID in .env as
GOOGLE_SHEET_ID (it's the long string in the sheet's URL between /d/ and /edit).
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import google_auth  # noqa: E402

LEADS = "Leads"
OUTBOX = "Outbox"
SUPPRESSION = "Suppression"

# Lifecycle values in Outbox.status. Claude writes DRAFT; the sender writes the rest.
# None of them arm a message — approval lives in its own column (see approved_variant).
DRAFT = "DRAFT"
SENT = "SENT"
CANCELLED = "CANCELLED"
FAILED = "FAILED"
HOLD = "HOLD"

# The three drafted versions of each message.
VARIANTS = (1, 2, 3)

# What Maha can put in `approve`. Picking one of the APPROVE_LABELS is the only thing
# in the entire system that causes mail to leave.
APPROVE_LABELS = {"Approve Draft 1": 1, "Approve Draft 2": 2, "Approve Draft 3": 3}
REJECT = "Reject"
REDRAFT = "Redraft"
APPROVE_OPTIONS = list(APPROVE_LABELS) + [REJECT, REDRAFT]

TABS = {
    LEADS: ["email", "domain", "company", "first_name", "title", "icp", "batch_id",
            "signal", "status", "date_added"],
    OUTBOX: ["row_key", "email", "first_name", "company", "icp", "icp_evidence",
             "channel", "linkedin_url", "li_note", "li_dm", "li_status", "signal",
             "subject_1", "body_1", "subject_2", "body_2", "subject_3", "body_3",
             "approve", "status", "sent_variant", "sent_at", "thread_id",
             "rfc_message_id", "note"],
    SUPPRESSION: ["email", "domain", "reason", "date_added"],
}

# LinkedIn is hand-sent, always (CLAUDE.md §1/§7). li_status is Maha's own record of
# what she has done there; no code in this repo ever writes it or sends on LinkedIn.
LI_STATUSES = ["To send", "Sent", "Accepted", "Replied", "Skipped"]
LI_NOTE_LIMIT = 300  # LinkedIn's hard cap on a connection request note.


def approved_variant(row):
    """Which draft this row is cleared to send, or None.

    This is the whole safety gate, so it fails closed on anything it does not
    positively recognise: blank, Reject, Redraft, a stray note, 'Approve Draft 4', a
    formula error. Only an exact APPROVE_LABELS entry arms a row.

    Bare '1'/'2'/'3' still work. Rows pushed before the labelled dropdown existed carry
    those, and silently ignoring them would strand approvals that were already given.
    """
    raw = (row.get("approve") or "").strip()
    if not raw:
        return None
    for label, n in APPROVE_LABELS.items():
        if raw.lower() == label.lower():
            return n
    try:
        # Sheets can hand back a typed 1 as '1.0'.
        n = int(float(raw))
    except (TypeError, ValueError):
        return None
    return n if n in VARIANTS else None


def approve_action(row):
    """What the reviewer asked for: 'approve', 'reject', 'redraft', or None."""
    raw = (row.get("approve") or "").strip().lower()
    if not raw:
        return None
    if approved_variant(row) is not None:
        return "approve"
    if raw == REJECT.lower():
        return "reject"
    if raw == REDRAFT.lower():
        return "redraft"
    return None


def sheet_id():
    """Accepts either the bare spreadsheet id or a pasted sheet URL — copying the
    whole URL out of the address bar is the natural thing to do."""
    raw = config.require("GOOGLE_SHEET_ID", "read and write the CRM sheet").strip()
    found = re.search(r"/d/([a-zA-Z0-9\-_]+)", raw)
    return found.group(1) if found else raw


def _api():
    return google_auth.sheets().spreadsheets()


def _run(request, attempts=4):
    """Execute a Sheets request, retrying transient network and 5xx failures.

    send_approved.py runs unattended on cron; a dropped connection partway through a
    batch would otherwise abort the run and leave rows in an unclear state. Retries
    are safe here because every call this module makes is idempotent — reads, header
    writes, and cell updates by explicit range.
    """
    import random
    import socket
    import time

    delay = 2.0
    for attempt in range(1, attempts + 1):
        try:
            return request.execute()
        except (socket.timeout, socket.error, TimeoutError, ConnectionError) as e:
            if attempt == attempts:
                raise
            wait = delay + random.uniform(0, 1)
            print(f"    sheets: {type(e).__name__}, retry {attempt}/{attempts - 1} in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2
        except Exception as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status not in (429, 500, 502, 503, 504) or attempt == attempts:
                raise
            wait = delay + random.uniform(0, 1)
            print(f"    sheets: HTTP {status}, retry {attempt}/{attempts - 1} in {wait:.0f}s")
            time.sleep(wait)
            delay *= 2


def ensure_tabs(spreadsheet_id=None):
    """Create any missing tab and write its header row. Safe to re-run."""
    sid = spreadsheet_id or sheet_id()
    meta = _run(_api().get(spreadsheetId=sid))
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}

    requests = [{"addSheet": {"properties": {"title": t}}} for t in TABS if t not in existing]
    if requests:
        _run(_api().batchUpdate(spreadsheetId=sid, body={"requests": requests}))

    for tab, headers in TABS.items():
        current = (_run(_api().values().get(spreadsheetId=sid, range=f"{tab}!1:1")) or {}).get("values", [])
        current = current[0] if current else []
        if current == headers:
            continue
        # An empty tab has nothing to describe, so its header row can be replaced
        # freely — this is what lets the schema change without a manual migration.
        # The moment there is one data row, the guard below takes over.
        has_data = len((_run(_api().values().get(
            spreadsheetId=sid, range=f"{tab}!A1:A")) or {}).get("values", [])) > 1
        if current and has_data and set(current) & set(headers):
            # Headers exist but differ — someone renamed or reordered columns. Blindly
            # rewriting the header row would leave it describing data that sits in
            # different columns, which is worse than stopping.
            missing = [h for h in headers if h not in current]
            raise SystemExit(
                f"\nThe '{tab}' tab's header row does not match what the code expects.\n"
                f"  in sheet : {', '.join(current)}\n"
                f"  expected : {', '.join(headers)}\n"
                + (f"  missing  : {', '.join(missing)}\n" if missing else "")
                + "Restore the header row by hand, then re-run.\n"
            )
        _run(_api().values().update(
            spreadsheetId=sid, range=f"{tab}!A1",
            valueInputOption="RAW", body={"values": [headers]},
        ))
    _header_cache.clear()
    return sid


def _tab_gids(sid):
    meta = _run(_api().get(spreadsheetId=sid))
    return {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}


def _bg(r, g, b):
    return {"backgroundColor": {"red": r, "green": g, "blue": b}}


def _clear_conditional_formats(sid, gid, meta=None):
    """Requests that strip every conditional format rule off a tab.

    format_tabs is safe to re-run, but addConditionalFormatRule *appends* — without
    this, each run stacks another copy of the palette, and rules written against an
    older column layout survive to colour rows by whatever now sits in that column.
    Deleted back-to-front because each removal reindexes the ones after it.
    """
    meta = meta or _run(_api().get(spreadsheetId=sid))
    for s in meta.get("sheets", []):
        if s["properties"]["sheetId"] == gid:
            n = len(s.get("conditionalFormats", []))
            return [{"deleteConditionalFormatRule": {"sheetId": gid, "index": i}}
                    for i in range(n - 1, -1, -1)]
    return []


def _this_row(col_index):
    """A1-style reference to `col_index` on whichever row the rule is evaluating.

    Deliberately not the plain '$M2' form. Sheets rewrites relative references in a
    conditional-format formula when rows are inserted, and push_to_sheet appends with
    INSERT_ROWS — so '$M2' silently became '$M3' and every row was being coloured by
    the state of the row *below* it. A whole-column reference is immune to that, and
    ROW() re-resolves per row.
    """
    letter = _column_letter(col_index)
    return "INDEX($%s:$%s,ROW())" % (letter, letter)


def format_tabs(spreadsheet_id=None):
    """Make the Outbox usable as an approval surface.

    Approval is the one manual step in the whole system, so it should be a dropdown
    click rather than typing a magic word — a typo silently means 'do not send', which
    is safe but confusing. Colour tells you the state of a row at a glance.
    """
    sid = spreadsheet_id or sheet_id()
    meta = _run(_api().get(spreadsheetId=sid))
    gids = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    if OUTBOX not in gids:
        raise SystemExit(
            f"\nThere is no '{OUTBOX}' tab in this spreadsheet yet.\n"
            "Run --init first:  .venv/bin/python scripts/sheets.py --init --format\n"
        )
    out = gids[OUTBOX]
    cols = TABS[OUTBOX]
    status_col = cols.index("status")
    approve_col = cols.index("approve")
    approve_ref = _this_row(approve_col)
    status_ref = _this_row(status_col)
    reqs = _clear_conditional_formats(sid, out, meta)

    # Header row frozen and bold on every tab.
    for tab, gid in gids.items():
        if tab not in TABS:
            continue
        reqs.append({"updateSheetProperties": {
            "properties": {"sheetId": gid, "gridProperties": {"frozenRowCount": 1}},
            "fields": "gridProperties.frozenRowCount"}})
        reqs.append({"repeatCell": {
            "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
            "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                                           **_bg(0.93, 0.93, 0.93)}},
            "fields": "userEnteredFormat(textFormat,backgroundColor)"}})

    # `approve` is the trigger: pick which draft goes out, or reject/redraft. A
    # dropdown rather than free text, because a typo here is the difference between
    # sending and not sending.
    reqs.append({"setDataValidation": {
        "range": {"sheetId": out, "startRowIndex": 1,
                  "startColumnIndex": approve_col, "endColumnIndex": approve_col + 1},
        "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
            {"userEnteredValue": v} for v in APPROVE_OPTIONS]},
            "showCustomUi": True, "strict": False}}})

    # LinkedIn tracking is Maha's to maintain by hand — nothing automates this channel.
    li_col = cols.index("li_status")
    reqs.append({"setDataValidation": {
        "range": {"sheetId": out, "startRowIndex": 1,
                  "startColumnIndex": li_col, "endColumnIndex": li_col + 1},
        "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
            {"userEnteredValue": v} for v in LI_STATUSES]},
            "showCustomUi": True, "strict": False}}})

    # status is the lifecycle record, written by the sender. Left editable so a row
    # can be manually parked on HOLD or CANCELLED, but nothing here arms a send.
    reqs.append({"setDataValidation": {
        "range": {"sheetId": out, "startRowIndex": 1,
                  "startColumnIndex": status_col, "endColumnIndex": status_col + 1},
        "rule": {"condition": {"type": "ONE_OF_LIST", "values": [
            {"userEnteredValue": v} for v in (DRAFT, HOLD, CANCELLED, SENT, FAILED)]},
            "showCustomUi": True, "strict": False}}})

    # Colour by state. "Armed" is deliberately the loudest: a draft is chosen and the
    # row is still live, so this message leaves on the next send run. Only the three
    # Approve labels count — Reject and Redraft must never look armed.
    live = 'OR(%s="%s", %s="")' % (status_ref, DRAFT, status_ref)
    approved_any = "OR(%s)" % ", ".join(
        '%s="%s"' % (approve_ref, label) for label in APPROVE_LABELS)
    palette = [
        ('=AND(%s, %s)' % (approved_any, live), _bg(0.79, 0.94, 0.79)),
        ('=AND(%s="%s", %s)' % (approve_ref, REDRAFT, live), _bg(1.00, 0.90, 0.72)),
        ('=AND(%s="%s", %s)' % (approve_ref, REJECT, live), _bg(0.93, 0.85, 0.92)),
        ('=%s="%s"' % (status_ref, SENT), _bg(0.90, 0.90, 0.90)),
        ('=%s="%s"' % (status_ref, HOLD), _bg(1.00, 0.95, 0.80)),
        ('=%s="%s"' % (status_ref, CANCELLED), _bg(0.96, 0.87, 0.87)),
        ('=%s="%s"' % (status_ref, FAILED), _bg(0.96, 0.80, 0.80)),
    ]
    for i, (formula, fmt) in enumerate(palette):
        reqs.append({"addConditionalFormatRule": {"index": i, "rule": {
            "ranges": [{"sheetId": out, "startRowIndex": 1,
                        "startColumnIndex": 0, "endColumnIndex": len(cols)}],
            "booleanRule": {
                "condition": {"type": "CUSTOM_FORMULA",
                              "values": [{"userEnteredValue": formula}]},
                "format": fmt}}}})

    # Readable widths; the three body columns wrap instead of running off the screen.
    widths = {"row_key": 40, "email": 200, "first_name": 90, "company": 130, "icp": 60,
              "icp_evidence": 210, "channel": 100, "linkedin_url": 190, "li_note": 320,
              "li_dm": 320, "li_status": 90,
              "signal": 260, "subject_1": 170, "body_1": 380, "subject_2": 170,
              "body_2": 380, "subject_3": 170, "body_3": 380, "approve": 140,
              "status": 100, "sent_variant": 60, "sent_at": 140, "thread_id": 40,
              "rfc_message_id": 40, "note": 200}
    for name, px in widths.items():
        idx = cols.index(name)
        reqs.append({"updateDimensionProperties": {
            "range": {"sheetId": out, "dimension": "COLUMNS",
                      "startIndex": idx, "endIndex": idx + 1},
            "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    for name in ("signal", "li_note", "li_dm", "body_1", "body_2", "body_3"):
        idx = cols.index(name)
        reqs.append({"repeatCell": {
            "range": {"sheetId": out, "startRowIndex": 1,
                      "startColumnIndex": idx, "endColumnIndex": idx + 1},
            "cell": {"userEnteredFormat": {"wrapStrategy": "WRAP",
                                           "verticalAlignment": "TOP"}},
            "fields": "userEnteredFormat(wrapStrategy,verticalAlignment)"}})
    # The approve cell should read as a button, not a data field.
    reqs.append({"repeatCell": {
        "range": {"sheetId": out, "startRowIndex": 1,
                  "startColumnIndex": approve_col, "endColumnIndex": approve_col + 1},
        "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER",
                                       "textFormat": {"bold": True}}},
        "fields": "userEnteredFormat(horizontalAlignment,textFormat)"}})

    _run(_api().batchUpdate(spreadsheetId=sid, body={"requests": reqs}))
    return len(reqs)


def read(tab, spreadsheet_id=None):
    """Return list of dicts. Each carries _row (1-based sheet row) for write-back."""
    sid = spreadsheet_id or sheet_id()
    values = (_run(_api().values().get(spreadsheetId=sid, range=tab)) or {}).get("values", [])
    if not values:
        return []
    headers = values[0]
    rows = []
    for i, raw in enumerate(values[1:], start=2):
        raw = list(raw) + [""] * (len(headers) - len(raw))
        row = dict(zip(headers, raw))
        row["_row"] = i
        rows.append(row)
    return rows


def _key_column(tab):
    """The column that identifies a row for dedup purposes, if the tab has one."""
    return "row_key" if tab == OUTBOX else ("email" if tab in (LEADS, SUPPRESSION) else None)


def _existing_keys(tab, sid):
    col = _key_column(tab)
    if not col:
        return None
    return {(r.get(col) or "").strip().lower() for r in read(tab, sid) if (r.get(col) or "").strip()}


def append(tab, dicts, spreadsheet_id=None):
    """Append rows, ordered to match the tab's headers. Safe to retry.

    Retrying an append is NOT inherently safe — unlike every other call in this module
    it is not idempotent, and `_run` retries transient failures. A timeout raised after
    the server had already appended would run the whole write a second time, giving one
    lead two Outbox rows. Two identical rows both look approvable, so the same person
    gets mailed twice — the exact thing the one-row-per-lead rule exists to prevent.

    So the retry happens here instead, around a re-check: each attempt first re-reads
    the tab's existing keys and drops anything already present. A duplicate created by
    a half-completed attempt is therefore skipped by the next one.
    """
    if not dicts:
        return 0
    sid = spreadsheet_id or sheet_id()
    # Real header order, not the canonical list — a reordered column would otherwise
    # put every value in the wrong field.
    headers = actual_headers(tab, sid) or TABS[tab]
    key_col = _key_column(tab)

    import random
    import socket
    import time

    delay, attempts = 2.0, 3
    for attempt in range(1, attempts + 1):
        pending = dicts
        if key_col:
            have = _existing_keys(tab, sid)
            pending = [d for d in dicts
                       if (d.get(key_col) or "").strip().lower() not in have]
        if not pending:
            return 0
        values = [[str(d.get(h, "")) for h in headers] for d in pending]
        try:
            # Deliberately NOT insertDataOption="INSERT_ROWS". That inserts brand-new
            # grid rows, and a new row carries none of the sheet's formatting: the
            # approve and li_status dropdowns are attached to a row range, so inserting
            # pushed the validated range down and left every freshly added lead with a
            # plain free-text cell. Writing into the existing empty rows keeps the
            # validation that is already on them.
            _run(_api().values().append(
                spreadsheetId=sid, range=f"{tab}!A1",
                valueInputOption="RAW",
                body={"values": values},
            ), attempts=1)
            return len(values)
        except (socket.timeout, socket.error, TimeoutError, ConnectionError) as e:
            err = e
        except Exception as e:
            status = getattr(getattr(e, "resp", None), "status", None)
            if status not in (429, 500, 502, 503, 504):
                raise
            err = e
        if attempt == attempts:
            raise err
        wait = delay + random.uniform(0, 1)
        print(f"    sheets: append {type(err).__name__}, re-checking then retrying in {wait:.0f}s")
        time.sleep(wait)
        delay *= 2
    return 0


def _column_letter(idx):
    letter = ""
    n = idx
    while True:
        letter = chr(ord("A") + n % 26) + letter
        n = n // 26 - 1
        if n < 0:
            break
    return letter


_header_cache = {}


def actual_headers(tab, spreadsheet_id=None):
    """The tab's real header row, cached for the run.

    Must not be assumed equal to TABS[tab]: if a column gets dragged to a new position
    in the sheet, the canonical order is wrong and a write computed from it lands in
    the wrong column — e.g. stamping SENT over someone's message body.
    """
    sid = spreadsheet_id or sheet_id()
    key = (sid, tab)
    if key not in _header_cache:
        got = _run(_api().values().get(spreadsheetId=sid, range=f"{tab}!1:1")) or {}
        rows = got.get("values") or [[]]
        _header_cache[key] = rows[0] if rows else []
    return _header_cache[key]


def invalidate_header_cache():
    """Drop the cached header row. Callers that live for hours (send_approved --watch)
    must do this each cycle: a column reordered mid-run would otherwise be written
    using the order captured when the process started."""
    _header_cache.clear()


def update_cells(tab, updates, spreadsheet_id=None):
    """updates: list of (row_number, column_name, value). One batched call."""
    if not updates:
        return 0
    sid = spreadsheet_id or sheet_id()
    headers = actual_headers(tab, sid)
    data = []
    for row_num, col, value in updates:
        if col not in headers:
            raise SystemExit(
                f"Column '{col}' is missing from the {tab} tab. Restore the header row "
                f"(expected: {', '.join(TABS[tab])}) before running again."
            )
        letter = _column_letter(headers.index(col))
        data.append({"range": f"{tab}!{letter}{row_num}", "values": [[str(value)]]})
    _run(_api().values().batchUpdate(
        spreadsheetId=sid, body={"valueInputOption": "RAW", "data": data},
    ))
    return len(data)


def resolve_row(row_key, expected_row, spreadsheet_id=None):
    """Return the sheet row that currently holds this row_key, or None if it's gone.

    Row numbers captured at read time go stale the moment someone sorts the sheet or
    inserts a row — and the sender reads once, then writes back over several minutes.
    Writing SENT to a stale row would mark the wrong person as contacted: that lead
    never gets their message, and the one we actually emailed stays armed and gets
    emailed again. Cheap single-cell check first, full rescan only if it moved.
    """
    sid = spreadsheet_id or sheet_id()
    try:
        got = _run(_api().values().get(spreadsheetId=sid, range=f"{OUTBOX}!A{expected_row}")) or {}
        values = got.get("values") or []
        if values and values[0] and values[0][0] == row_key:
            return expected_row
    except Exception:
        pass
    for r in read(OUTBOX, sid):
        if (r.get("row_key") or "") == row_key:
            return r["_row"]
    return None


def suppressed_emails(spreadsheet_id=None):
    """Emails and domains to never contact. Read fresh at send time.

    Domains are reduced to eTLD+1 to match how lead domains are derived — otherwise a
    suppression entry of 'shop.brand.com' silently fails to block 'brand.com'.
    """
    from process_batch import FREEMAIL, registrable_domain

    emails, domains = set(), set()
    for r in read(SUPPRESSION, spreadsheet_id):
        e = (r.get("email") or "").strip().lower()
        d = (r.get("domain") or "").strip().lower()
        if e:
            emails.add(e)
            host = e.split("@")[-1]
            # An opt-out blocks the person; it blocks the company only when the
            # address is on a company domain, not a personal mailbox.
            if host and host not in FREEMAIL and not d:
                domains.add(registrable_domain(host))
        if d:
            domains.add(registrable_domain(d))
    return emails, domains


def set_lead_status(email, status, spreadsheet_id=None):
    """Move a lead to a new pipeline stage on the Leads tab. Returns True if found.

    The Outbox is the working queue; the Leads tab is the CRM record (CLAUDE.md §12).
    Without this the CRM sits on "Drafted, pending review" forever — including for
    people who have already been emailed, replied, or opted out.

    Looked up fresh rather than cached: a stale row number here would stamp the wrong
    person's record.
    """
    email = (email or "").strip().lower()
    if not email:
        return False
    for r in read(LEADS, spreadsheet_id):
        if (r.get("email") or "").strip().lower() == email:
            update_cells(LEADS, [(r["_row"], "status", status)], spreadsheet_id)
            return True
    return False


def existing_row_keys(spreadsheet_id=None):
    """row_keys already in Outbox — prevents pushing the same draft twice."""
    return {(r.get("row_key") or "").strip() for r in read(OUTBOX, spreadsheet_id) if r.get("row_key")}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true", help="create tabs and headers")
    ap.add_argument("--format", action="store_true",
                    help="apply the approval dropdown, colours, and column widths")
    args = ap.parse_args()

    sid = sheet_id()
    if args.init:
        ensure_tabs(sid)
        print(f"\nTabs ready: {', '.join(TABS)}")
    if args.format:
        n = format_tabs(sid)
        print(f"Formatting applied ({n} changes): status dropdown, colour by state, "
              f"frozen headers, wrapped body column.")
    for tab in TABS:
        print(f"  {tab:12} {len(read(tab, sid))} rows")
    print(f"\nhttps://docs.google.com/spreadsheets/d/{sid}/edit\n")


if __name__ == "__main__":
    main()
