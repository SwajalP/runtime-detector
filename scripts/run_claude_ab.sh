#!/usr/bin/env bash
# Hook-schema A/B for renewal-discount. Does not require a Claude login.
# If `claude` is logged in, `ledger ab` also stores that live run separately.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [[ -f "$ROOT/.venv/bin/activate" ]]; then
  # shellcheck disable=SC1091
  source "$ROOT/.venv/bin/activate"
fi
cd "$ROOT"
exec ledger ab --task renewal-discount --repo "$ROOT/demo_repo" "$@"
