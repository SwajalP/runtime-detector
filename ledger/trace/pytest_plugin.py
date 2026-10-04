"""Pytest plugin: per-test coverage contexts + failure frames → LEDGER trace files.

Activated only when ``LEDGER_SESSION_ID`` is set (so ordinary pytest runs are
untouched). Writes into ``LEDGER_TRACE_DIR``:

  pytest-report.jsonl     one line per test: outcome + traceback frames
  coverage-contexts.json  {nodeid: {abs_path: [executed lines]}}
  coverage-lines.json     {abs_path: {line: hits}} (union)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


def _enabled() -> bool:
    return bool(os.environ.get("LEDGER_SESSION_ID"))


def _trace_dir() -> Path:
    p = Path(os.environ.get("LEDGER_TRACE_DIR", "."))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _cov():
    try:
        from coverage import Coverage

        return Coverage.current()
    except Exception:
        return None


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if not _enabled():
        return
    cov = _cov()
    if cov is not None:
        try:
            cov.switch_context(item.nodeid)
        except Exception:
            pass


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if not _enabled() or report.when != "call":
        return
    frames = []
    if report.failed and report.longrepr is not None:
        tb = getattr(report.longrepr, "reprtraceback", None)
        if tb is not None:
            for entry in getattr(tb, "reprentries", []):
                loc = getattr(entry, "reprfileloc", None)
                if loc is not None:
                    frames.append({"path": loc.path, "line": loc.lineno, "message": (loc.message or "")[:200]})
        # exception chain / crash location
        crash = getattr(report.longrepr, "reprcrash", None)
        if crash is not None and not any(f["path"] == crash.path and f["line"] == crash.lineno for f in frames):
            frames.append({"path": crash.path, "line": crash.lineno, "message": (crash.message or "")[:200]})
    rec = {
        "session_id": os.environ.get("LEDGER_SESSION_ID"),
        "nodeid": item.nodeid,
        "outcome": report.outcome,
        "duration_ms": int(report.duration * 1000),
        "longrepr": str(report.longrepr)[:2000] if report.failed else None,
        "frames": frames,
    }
    with (_trace_dir() / "pytest-report.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def pytest_sessionfinish(session, exitstatus):
    if not _enabled():
        return
    cov = _cov()
    if cov is None:
        return
    try:
        cov.switch_context("")
        data = cov.get_data()
        union: dict[str, dict[int, int]] = {}
        by_ctx: dict[str, dict[str, list[int]]] = {}
        contexts = sorted(c for c in data.measured_contexts() if c)
        for ctx in contexts:
            data.set_query_context(ctx)
            per_file: dict[str, list[int]] = {}
            for filename in data.measured_files():
                lines = data.lines(filename) or []
                if lines:
                    per_file[filename] = sorted(lines)
                    bucket = union.setdefault(filename, {})
                    for ln in lines:
                        bucket[ln] = bucket.get(ln, 0) + 1
            by_ctx[ctx] = per_file
        data.set_query_contexts(None)
        if not union:
            for filename in data.measured_files():
                union[filename] = {ln: 1 for ln in (data.lines(filename) or [])}
        out = _trace_dir()
        (out / "coverage-lines.json").write_text(json.dumps(union), encoding="utf-8")
        (out / "coverage-contexts.json").write_text(json.dumps(by_ctx), encoding="utf-8")
    except Exception as exc:  # never break the user's test run
        (_trace_dir() / "plugin-error.txt").write_text(repr(exc))
