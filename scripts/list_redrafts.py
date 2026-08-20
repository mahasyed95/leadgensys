#!/usr/bin/env python3
"""
List Outbox rows marked `Redraft`, with their current copy.

    .venv/bin/python scripts/list_redrafts.py

Marking a row `Redraft` means the research was fine but the copy missed. This prints
what is there now so Claude can rewrite the three variants in place. After rewriting,
clear the row's `approve` cell back to blank so it reads as unreviewed again.

Nothing here sends, cancels, or edits anything — it is a read-only worklist.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sheets  # noqa: E402


def redraft_rows(rows):
    live = ("", sheets.DRAFT)
    return [r for r in rows
            if (r.get("status") or "").strip().upper() in live
            and sheets.approve_action(r) == "redraft"]


def main():
    rows = redraft_rows(sheets.read(sheets.OUTBOX))
    if not rows:
        print("\nNothing marked Redraft.\n")
        return

    print(f"\n{len(rows)} row(s) awaiting a rewrite:\n")
    for r in rows:
        print(f"  {r.get('company', '?')}  <{r.get('email', '?')}>   row {r['_row']}")
        print(f"    icp     : {r.get('icp', '')}  {r.get('icp_evidence', '')}")
        print(f"    signal  : {r.get('signal', '')}")
        for n in sheets.VARIANTS:
            print(f"    {n}. {r.get('subject_%d' % n, '')}")
            body = (r.get("body_%d" % n) or "").replace("\n", " ")
            print(f"       {body[:150]}{'...' if len(body) > 150 else ''}")
        print()

    print("Rewrite the variants for these rows, then blank their `approve` cell.\n")


if __name__ == "__main__":
    main()
