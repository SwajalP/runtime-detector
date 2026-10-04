"""Map executed (file, line) pairs onto indexed source regions."""

from __future__ import annotations

import json
from pathlib import Path

from ledger.index.symbols import region_covering


def map_coverage_to_regions(conn, cfg, cov_data: dict, session_id: str | None = None) -> list[str]:
    """cov_data: {rel_path: {line_no: hits}} → ordered unique region ids.

    Module-level regions are skipped (import lines execute on every run and
    carry no behavioural signal); only function/method/class regions count.
    """
    region_ids: list[str] = []
    for rel, lines in cov_data.items():
        rel = _normalize(cfg.repo_root, rel)
        seen_in_file: set[str] = set()
        for line, hits in sorted(lines.items(), key=lambda kv: int(kv[0])):
            if not hits:
                continue
            region = region_covering(conn, rel, int(line))
            if region and region["kind"] != "module" and region["region_id"] not in seen_in_file:
                seen_in_file.add(region["region_id"])
                region_ids.append(region["region_id"])
    return list(dict.fromkeys(region_ids))


def load_coverage_file(coverage_json_path: Path) -> dict:
    """Load a coverage.py JSON report into {path: {line: 1}}."""
    data = json.loads(coverage_json_path.read_text())
    files = data.get("files") or {}
    return {path: {int(ln): 1 for ln in (meta.get("executed_lines") or [])} for path, meta in files.items()}


def _normalize(root: Path, path: str) -> str:
    p = Path(path)
    try:
        resolved = p.resolve() if p.is_absolute() else (root / p).resolve()
        return str(resolved.relative_to(root.resolve()))
    except ValueError:
        return path.replace("\\", "/")
