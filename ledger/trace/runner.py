"""Run pytest under coverage and join the program trace with indexed source regions.

Produces three tiers of execution evidence:
  * ``frames``        – traceback frames of failing tests (pinned anchors)
  * ``failing_only``  – lines executed by failing tests but by no passing test
  * ``executed``      – everything else the targeted tests ran
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from ledger.config import LedgerConfig
from ledger.index.symbols import region_covering
from ledger.trace.coverage_mapper import map_coverage_to_regions

_FRAME_RX = re.compile(r"^(?P<path>[^\s:]+\.py):(?P<line>\d+)(?::\s*(?P<msg>.*))?$", re.M)


def pytest_args_from_command(command: str) -> list[str] | None:
    """Extract pytest args from a shell command the agent ran, or None."""
    if "pytest" not in command:
        return None
    # take the segment starting at 'pytest'
    seg = command[command.index("pytest") + len("pytest"):]
    seg = seg.split("&&")[0].split("||")[0].split("|")[0].split(";")[0]
    args = [a for a in seg.split() if a and a not in {"-q", "-x", "-s", "-v", "-vv", "--tb=short", "--tb=long", "--tb=no"}]
    return ["-q", *args]


def parse_frames_from_text(text: str, repo_root: Path) -> list[dict]:
    frames = []
    for m in _FRAME_RX.finditer(text or ""):
        rel = _rel(repo_root, m.group("path"))
        if rel and (repo_root / rel).exists():
            frames.append({"path": rel, "line": int(m.group("line")), "message": (m.group("msg") or "")[:200]})
    return frames


def run_pytest_traced(
    cfg: LedgerConfig,
    session_id: str,
    args: list[str] | None = None,
    conn=None,
    collector=None,
    controller=None,
    task_id: str | None = None,
    record_event: bool = True,
) -> dict:
    args = args or ["-q"]
    with tempfile.TemporaryDirectory(prefix="ledger-trace-") as tmp:
        trace_dir = Path(tmp)
        env = os.environ.copy()
        env.update(
            {
                "LEDGER_SESSION_ID": session_id,
                "LEDGER_DB": str(cfg.db_path),
                "LEDGER_REPO": str(cfg.repo_root),
                "LEDGER_TRACE_DIR": str(trace_dir),
                "COVERAGE_FILE": str(trace_dir / ".coverage"),
                "PYTHONPATH": os.pathsep.join([str(cfg.repo_root), env.get("PYTHONPATH", "")]).rstrip(os.pathsep),
            }
        )
        # The plugin is also registered via the `pytest11` entry point; `-p` makes
        # it work even when LEDGER is not installed into the target repo's venv.
        cmd = [sys.executable, "-m", "coverage", "run", "--source", str(cfg.repo_root), "-m", "pytest", "-p", "ledger.trace.pytest_plugin", *args]
        proc = subprocess.run(cmd, cwd=cfg.repo_root, env=env, capture_output=True, text=True, timeout=600)
        output = proc.stdout + proc.stderr

        union = _load(trace_dir / "coverage-lines.json") or {}
        by_ctx = _load(trace_dir / "coverage-contexts.json") or {}
        if not union:
            cov_json = trace_dir / "cov.json"
            subprocess.run([sys.executable, "-m", "coverage", "json", "-o", str(cov_json)], cwd=cfg.repo_root, env=env, capture_output=True, text=True)
            payload = _load(cov_json) or {}
            union = {p: {str(ln): 1 for ln in (m.get("executed_lines") or [])} for p, m in (payload.get("files") or {}).items()}

        reports = []
        rp = trace_dir / "pytest-report.jsonl"
        if rp.exists():
            reports = [json.loads(l) for l in rp.read_text().splitlines() if l.strip()]
        failing = {r["nodeid"] for r in reports if r["outcome"] == "failed"}
        passing = {r["nodeid"] for r in reports if r["outcome"] == "passed"}

        def to_rel_cov(files: dict) -> dict[str, dict[int, int]]:
            out: dict[str, dict[int, int]] = {}
            for filename, lines in files.items():
                rel = _rel(cfg.repo_root, filename)
                if not rel:
                    continue
                if isinstance(lines, dict):
                    out[rel] = {int(k): int(v) for k, v in lines.items()}
                else:
                    out[rel] = {int(k): 1 for k in lines}
            return out

        rel_union = to_rel_cov(union)
        fail_lines: dict[str, set[int]] = {}
        pass_lines: dict[str, set[int]] = {}
        for ctx, files in by_ctx.items():
            bucket = fail_lines if ctx in failing else pass_lines if ctx in passing else None
            if bucket is None:
                continue
            for rel, lines in to_rel_cov(files).items():
                bucket.setdefault(rel, set()).update(lines)
        failing_only = {
            rel: {ln: 1 for ln in lines - pass_lines.get(rel, set())} for rel, lines in fail_lines.items()
        }
        failing_only = {k: v for k, v in failing_only.items() if v}

        frames: list[dict] = []
        for rec in reports:
            for fr in rec.get("frames") or []:
                rel = _rel(cfg.repo_root, fr.get("path") or "")
                if rel:
                    frames.append({**fr, "path": rel, "nodeid": rec["nodeid"]})
        if not frames and proc.returncode != 0:
            frames = parse_frames_from_text(output, cfg.repo_root)

        frame_ids: list[str] = []
        executed_ids: list[str] = []
        failing_only_ids: list[str] = []
        if conn is not None:
            for fr in frames:
                region = region_covering(conn, fr["path"], int(fr.get("line") or 1))
                if region:
                    fr["region_id"] = region["region_id"]
                    fr["symbol"] = region.get("symbol")
                    frame_ids.append(region["region_id"])
            failing_only_ids = map_coverage_to_regions(conn, cfg, failing_only, session_id)
            executed_ids = map_coverage_to_regions(conn, cfg, rel_union, session_id)
        frame_ids = list(dict.fromkeys(frame_ids))
        failing_only_ids = [r for r in dict.fromkeys(failing_only_ids) if r not in frame_ids]
        executed_ids = [r for r in dict.fromkeys(executed_ids) if r not in frame_ids and r not in failing_only_ids]
        all_ids = frame_ids + failing_only_ids + executed_ids

        result = {
            "returncode": proc.returncode,
            "passed": proc.returncode == 0,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-2000:],
            "tests": {"failed": sorted(failing), "passed": sorted(passing)},
            "frames": frames,
            "region_ids": all_ids,
            "frame_region_ids": frame_ids,
            "failing_only_region_ids": failing_only_ids,
            "executed_region_ids": executed_ids,
            "tokens": max(1, len(output) // 4),
        }
        if collector and record_event:
            collector.record(
                session_id=session_id,
                source="program_trace",
                operation="test",
                task_id=task_id,
                query="pytest " + " ".join(args),
                regions=all_ids,
                tokens_returned=result["tokens"],
                bytes_returned=len(output.encode()),
                success=proc.returncode == 0,
                extra={
                    "frames": [{k: v for k, v in f.items() if k != "message"} for f in frames][:10],
                    "returncode": proc.returncode,
                    "failed": sorted(failing),
                    "passed": sorted(passing),
                    "n_failing_only": len(failing_only_ids),
                    "n_executed": len(executed_ids),
                },
                bump=True,
            )
        if controller:
            if executed_ids:
                controller.observe_execution(session_id, executed_ids, high_priority=False)
            if failing_only_ids:
                controller.observe_execution(session_id, failing_only_ids, high_priority=True)
            if frame_ids:
                controller.observe_execution(session_id, frame_ids, high_priority=True)
        return result


def _load(path: Path):
    try:
        return json.loads(path.read_text()) if path.exists() else None
    except json.JSONDecodeError:
        return None


def _rel(root: Path, path: str) -> str | None:
    if not path:
        return None
    p = Path(path)
    try:
        if p.is_absolute():
            return str(p.resolve().relative_to(root.resolve()))
        candidate = (root / p).resolve()
        if candidate.exists():
            return str(candidate.relative_to(root.resolve()))
        return str(p)
    except ValueError:
        return None
