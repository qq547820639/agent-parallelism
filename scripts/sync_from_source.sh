#!/usr/bin/env bash
# Sync this distribution copy from the single source of truth.
#
# Source of truth : ~/.workbuddy/skills/agent-parallelism/   (daily iteration happens there)
# This repo       : distribution copy + a user-facing README
#
# NEVER edit SKILL.md / assets / references / scripts / evals in this repo directly --
# it will silently drift from the source. Edit the source, then run this script.
#
# File discovery is automatic: anything present in the source is synced. A hardcoded
# list was used previously and would have gone stale the moment a file was added.
#
# Written for bash 3.2 (what macOS ships with): no mapfile, no associative arrays.
#
# Usage:
#   bash scripts/sync_from_source.sh              # sync + self-check
#   bash scripts/sync_from_source.sh --dry-run    # show what would change
#   AGENT_PARALLELISM_SRC=/path/to/skill bash scripts/sync_from_source.sh

set -eu

SRC="${AGENT_PARALLELISM_SRC:-$HOME/.workbuddy/skills/agent-parallelism}"
DST="$(cd "$(dirname "$0")/.." && pwd)"
DRY=""
[ "${1:-}" = "--dry-run" ] && DRY=1

[ -d "$SRC" ] || { echo "ERROR: source not found: $SRC" >&2; exit 1; }
[ -f "$SRC/SKILL.md" ] || { echo "ERROR: $SRC does not look like the skill" >&2; exit 1; }

# Repo-side files that must never be overwritten by a sync.
REPO_ONLY_MATCH="scripts/sync_from_source.sh"

skip_file() {
  case "$1" in
    __pycache__*|*.pyc|*.pyo|*pre-merge*) return 0 ;;
    */.DS_Store|.DS_Store) return 0 ;;
    "$REPO_ONLY_MATCH") return 0 ;;
  esac
  return 1
}

LIST="$(mktemp)"
trap 'rm -f "$LIST"' EXIT
(cd "$SRC" && find . -type f | sed 's|^\./||' | sort) > "$LIST"

TOTAL=$(awk 'END{print NR}' "$LIST")
[ "$TOTAL" -gt 0 ] || { echo "ERROR: discovered no files in source" >&2; exit 1; }

echo "source: $SRC"
echo "target: $DST"
echo "discovered $TOTAL source files"
echo

changed=0
while IFS= read -r f; do
  [ -n "$f" ] || continue
  if skip_file "$f"; then continue; fi
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
done < "$LIST"

# Files superseded in the source must not linger here.
for f in assets/eval-cases.md; do
  if [ -f "$DST/$f" ]; then
    if [ -n "$DRY" ]; then
      echo "  would-rm  $f (superseded in source)"
    else
      rm "$DST/$f"
      echo "  removed   $f (superseded in source)"
    fi
    changed=$((changed + 1))
  fi
done

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
echo "  3. Independent check with the platform's own validator, and packaging:"
echo "       SC=/Applications/WorkBuddy.app/Contents/Resources/app.asar.unpacked/resources"
echo "       python3 \"\$SC/plugins/workbuddy-builtin/skills/skill-creator/scripts/quick_validate.py\" \"$SRC\""
echo "       python3 \"\$SC/plugins/workbuddy-builtin/skills/skill-creator/scripts/package_skill.py\" \\"
echo "         \"$SRC\" ./dist"
echo "  4. git add -A && git commit"
