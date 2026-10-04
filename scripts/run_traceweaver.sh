#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
traceweaver run --agent simulated --task renewal-discount --repo "$ROOT/demo_repo"
