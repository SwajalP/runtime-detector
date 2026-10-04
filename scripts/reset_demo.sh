#!/usr/bin/env bash
# One-click venue reset. Does not call Claude or any model API.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FIXTURE="$ROOT/fixtures/replay/renewal-discount.jsonl"

cd "$ROOT/demo_repo"
git checkout -- . 2>/dev/null || true
rm -rf .traceweaver
traceweaver init --repo "$ROOT/demo_repo"
echo "demo repo reset and re-indexed (offline; not a live model)"

if [[ ! -f "$FIXTURE" ]]; then
  echo "missing replay fixture: $FIXTURE" >&2
  exit 1
fi

echo "Offline REPLAY fixture: $FIXTURE"
echo "  traceweaver replay --repo $ROOT/demo_repo"
echo "  labelled REPLAY — recorded events, not live Claude"

if [[ "${1:-}" == "--replay" ]]; then
  traceweaver replay --speed 0 --repo "$ROOT/demo_repo"
  echo "replayed. serve with: traceweaver serve --repo $ROOT/demo_repo --port 8765"
fi
