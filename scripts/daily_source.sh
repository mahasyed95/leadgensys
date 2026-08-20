#!/bin/bash
# Daily lead sourcing. Pull -> dedupe -> qualify. Drafting is NOT done here.
#
# Install (runs 06:30 every day, local time):
#   crontab -e
#   30 6 * * * "/Users/mahasyed/Desktop/Claude Code (DO NOT TOUCH)/Outreach Sys/scripts/daily_source.sh"
#
# Each morning this leaves a qualified batch in Leads/Batches/ and appends to
# Leads/sourced-ledger.csv so tomorrow's run never re-buys the same companies.
# Ask Claude to research and draft the batch when you sit down — that step needs
# judgement about what counts as a real personalization signal, and CLAUDE.md §9
# says a lead with no genuine signal gets flagged, not given a generic opener.
#
# Nothing here sends anything. Nothing here touches the approve column.

set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
PY="$ROOT/.venv/bin/python"
LOG="$ROOT/Leads/daily.log"

LEADS_PER_DAY="${LEADS_PER_DAY:-15}"

# --- Run-once-a-day guard ------------------------------------------------------
# A second run on the same day is not harmless: apollo_pull spends a real credit per
# enriched lead, so a duplicate 06:30 firing costs 15 credits for leads we already
# hold. Two schedulers can plausibly point at this script at once (the launchd agent
# that replaced cron, plus a stale crontab line), and a run that overruns into the
# next day's slot would overlap itself. Both cases are handled here rather than by
# assuming only one caller exists.
#
# mkdir is the lock because it is atomic on every filesystem and macOS has no flock(1).
LOCK="$ROOT/Leads/.daily.lock"
STAMPFILE="$ROOT/Leads/.last-daily-run"
TODAY="$(date +%Y-%m-%d)"

if [ "${FORCE_DAILY:-0}" != "1" ]; then
  if [ -f "$STAMPFILE" ] && [ "$(cat "$STAMPFILE" 2>/dev/null)" = "$TODAY" ]; then
    echo "daily_source: already completed for $TODAY — skipping. (FORCE_DAILY=1 overrides.)"
    exit 0
  fi
fi

if ! mkdir "$LOCK" 2>/dev/null; then
  # A lock older than 6h is a crashed run, not a live one; a normal batch is minutes.
  if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +360 2>/dev/null)" ]; then
    echo "daily_source: clearing stale lock at $LOCK"
    rmdir "$LOCK" 2>/dev/null && mkdir "$LOCK" 2>/dev/null || exit 1
  else
    echo "daily_source: another run is in progress — skipping."
    exit 0
  fi
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Alternate ICP so both get a comparable sample (CLAUDE.md §8 diversification, and
# §14's "which ICP is converting"). Even day-of-year = ecom, odd = agency.
if [ $(( $(date +%j) % 2 )) -eq 0 ]; then ICP="ecom"; else ICP="agency"; fi

STAMP="$(date +%Y-%m-%d)"
OUT="$ROOT/Leads/Inbox/${STAMP}-${ICP}-apollo.csv"

{
  echo ""
  echo "=== daily_source $(date '+%Y-%m-%d %H:%M')  icp=$ICP  limit=$LEADS_PER_DAY ==="

  if [ ! -x "$PY" ]; then
    echo "FATAL: no venv python at $PY"
    exit 1
  fi

  # Already-pulled companies are skipped inside apollo_pull via sourced-ledger.csv,
  # so re-running on the same day costs nothing beyond the free search call.
  if ! "$PY" -W ignore "$ROOT/scripts/apollo_pull.py" \
        --icp "$ICP" --limit "$LEADS_PER_DAY" --per-page 60 --out "$OUT"; then
    echo "apollo_pull failed — stopping before qualification."
    exit 1
  fi

  if ! "$PY" -W ignore "$ROOT/scripts/process_batch.py" "$OUT" --icp "$ICP"; then
    echo "process_batch failed."
    exit 1
  fi

  # Stamped here, not at the end: this is the point where Apollo credits have actually
  # been spent. If drafting fails afterwards the batch still exists and can be drafted
  # by hand — re-pulling it would just buy the same leads a second time.
  echo "$TODAY" > "$STAMPFILE"

  # --- Drafting -----------------------------------------------------------------
  # Needs the Claude CLI. Research and copywriting are the parts that cannot be
  # scripted: the whole system depends on finding ONE true, specific signal per lead,
  # and a template that fills the gap when no signal exists is worse than no email at
  # all (CLAUDE.md §9). So if the CLI is absent we stop here rather than degrade.
  CLAUDE_BIN="$(command -v claude || true)"
  [ -z "$CLAUDE_BIN" ] && [ -x "$HOME/.local/bin/claude" ] && CLAUDE_BIN="$HOME/.local/bin/claude"

  if [ -z "$CLAUDE_BIN" ]; then
    echo "Batch qualified, but drafting was skipped: no 'claude' CLI on PATH."
    echo "Install it with:  curl -fsSL https://claude.ai/install.sh | bash"
    echo "Until then, ask Claude to draft this batch interactively."
    exit 0
  fi

  echo "Drafting with $CLAUDE_BIN ..."

  # Research tools must be granted explicitly. Without them the drafting agent cannot
  # open a single prospect site, and personalization is the entire point of this system:
  # the 2026-08-17 run reached 12 qualified leads and correctly refused to draft any of
  # them because WebFetch was not allowlisted. It was right to refuse — but the run was
  # wasted. Note this is a tool allowlist, not a widening of the approval gate: nothing
  # here lets the agent send, and send_approved.py is still the only sender.
  DRAFT_TOOLS="Read,Write,Edit,Glob,Grep,WebFetch,WebSearch,TodoWrite,Bash"

  BEFORE="$(ls -1 "$ROOT/Sequences/Drafts/" 2>/dev/null | wc -l | tr -d ' ')"

  if ! "$CLAUDE_BIN" -p "$(cat "$ROOT/scripts/daily_draft_prompt.md")" \
        --permission-mode acceptEdits --allowedTools "$DRAFT_TOOLS"; then
    echo "Drafting run failed. The qualified batch is still in Leads/Batches/ —"
    echo "nothing was lost, and nothing was sent."
    exit 1
  fi

  # A zero exit does not mean drafts exist. The agent exits 0 when it deliberately
  # declines to draft (no research signal, blocked tools), which is correct behaviour —
  # but the old code printed "Done. Drafts are in the Outbox" regardless, so the log's
  # final line asserted a sheet update that had not happened. Verify, then report.
  AFTER="$(ls -1 "$ROOT/Sequences/Drafts/" 2>/dev/null | wc -l | tr -d ' ')"
  if [ "$AFTER" -gt "$BEFORE" ]; then
    echo "Done. $((AFTER - BEFORE)) new draft file(s); Outbox rows are DRAFT with approve blank."
  else
    echo "INCOMPLETE: the batch was sourced and qualified, but NO drafts were produced"
    echo "and nothing was pushed to the Sheet. Read the drafting output above for why"
    echo "(usually: no genuine personalization signal, or a research tool was blocked)."
    echo "The batch is intact at Leads/Batches/ — re-run drafting, do not re-pull."
    exit 2
  fi
} >> "$LOG" 2>&1
