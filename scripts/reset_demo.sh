#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/demo_repo"
git checkout -- . 2>/dev/null || true
ledger init --repo "$ROOT/demo_repo"
echo "demo repo reset and re-indexed"
