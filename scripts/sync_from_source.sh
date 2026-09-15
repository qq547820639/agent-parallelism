#!/usr/bin/env bash
# Sync this distribution copy from the single source of truth.
#
# Source of truth : ~/.workbuddy/skills/agent-parallelism/   (daily iteration happens there)
# This repo       : distribution copy + a user-facing README
#
# NEVER edit SKILL.md / assets / references / scripts / evals in this repo directly --
# it will silently drift from the source. Edit the source, then run this script.
#
# Deliberately NOT touched: README.md, .codebuddy/, .git/, scripts/sync_from_source.sh
#
# Usage:
#   bash scripts/sync_from_source.sh              # sync + self-check
#   bash scripts/sync_from_source.sh --dry-run    # show what would change
#   AGENT_PARALLELISM_SRC=/path/to/skill bash scripts/sync_from_source.sh

set -euo pipefail

SRC="${AGENT_PARALLELISM_SRC:-$HOME/.workbuddy/skills/agent-parallelism}"
DST="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY=1

[ -d "$SRC" ] || { echo "ERROR: source not found: $SRC" >&2; exit 1; }
[ -f "$SRC/SKILL.md" ] || { echo "ERROR: $SRC does not look like the skill" >&2; exit 1; }

FILES=(
  SKILL.md
  assets/contract-template.md
  assets/dispatch-checklist.md
  assets/subagent-brief-template.md
  assets/parallel-log.csv
  references/关键数字速查.md
  references/研究依据_多Agent并行.md
  scripts/fit_kappa.py
  scripts/selfcheck.py
  evals/README.md
  evals/evals.json
  evals/eval_queries.json
)

echo "source: $SRC"
echo "target: $DST"
echo

changed=0
for f in "${FILES[@]}"; do
  if [ ! -f "$SRC/$f" ]; then
    echo "  MISSING in source: $f" >&2
    exit 1
  fi
  mkdir -p "$DST/$(dirname "$f")"
  if [ -f "$DST/$f" ] && cmp -s "$SRC/$f" "$DST/$f"; then
    printf "  same      %s\n" "$f"
  else
    if [ -n "$DRY" ]; then
      printf "  would-cp  %s\n" "$f"
    else
      cp "$SRC/$f" "$DST/$f"
      printf "  copied    %s\n" "$f"
    fi
    changed=$((changed + 1))
  fi
done

# Files superseded in the source must not linger here.
if [ -f "$DST/assets/eval-cases.md" ]; then
  if [ -n "$DRY" ]; then
    echo "  would-rm  assets/eval-cases.md (superseded by evals/)"
  else
    rm "$DST/assets/eval-cases.md"
    echo "  removed   assets/eval-cases.md (superseded by evals/)"
  fi
  changed=$((changed + 1))
fi

echo
echo "changed: $changed"
if [ -n "$DRY" ]; then
  echo "(dry run -- nothing was written)"
  exit 0
fi

echo
echo "== verifying inside this repo =="
python3 "$DST/scripts/selfcheck.py"

echo
echo "Next:"
echo "  1. git status && git diff --stat"
echo "  2. README.md is maintained HERE ONLY -- a content sync can leave it stale."
echo "     Diff it against the change list before committing."
echo "  3. git add -A -- . ':!.codebuddy' && git commit"
