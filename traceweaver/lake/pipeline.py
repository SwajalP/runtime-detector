"""Materialize bronze / silver / gold from the SQLite event log.

Bronze is an append-only export of ``events``. Existing bronze lines are
never rewritten. Silver joins ``agent_tool`` regions that also appear in
``program_trace`` onto ``source_regions``. Gold is the working-set snapshot,
the latest bundle, the structural audit, and A/B metric rows.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from traceweaver.config import TraceWeaverConfig
from traceweaver.events.redact import redact_text, should_redact_path
from traceweaver.events.schema import event_from_row, now_ms

BRONZE_NAME = "events.jsonl"
SILVER_NAME = "region_observations.jsonl"
GOLD_WORKING_SET = "working_set.jsonl"
GOLD_BUNDLES = "bundles.jsonl"
GOLD_METRICS = "metrics.jsonl"


def lake_root(cfg: TraceWeaverConfig) -> Path:
    return cfg.traceweaver_dir / "lake"


def backend_status() -> dict[str, Any]:
    """Local lake only. A set Databricks host is not a job run."""
    host = os.environ.get("DATABRICKS_HOST", "").strip()
    token = os.environ.get("DATABRICKS_TOKEN", "").strip()
    configured = bool(host and token)
    if configured:
        note = (
            "DATABRICKS_HOST is set. Databricks was not called. "
            "Ingest the local JSONL layout described in docs/LAKE.md."
        )
    else:
        note = "DATABRICKS_HOST and token are unset."
    return {
        "backend": "local-lake",
        "databricks_configured": configured,
        "databricks_called": False,
        "note": note,
    }


def _count_jsonl(path: Path) -> int:
    if not path.is_file():
        return 0
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(json.dumps(row, sort_keys=True, default=str) + "\n" for row in rows)
    path.write_text(body, encoding="utf-8")


def lake_summary(cfg: TraceWeaverConfig) -> dict[str, Any]:
    root = lake_root(cfg)
    bronze = _count_jsonl(root / "bronze" / BRONZE_NAME)
    silver = _count_jsonl(root / "silver" / SILVER_NAME)
    working_set = _count_jsonl(root / "gold" / GOLD_WORKING_SET)
    bundles = _count_jsonl(root / "gold" / GOLD_BUNDLES)
    metrics = _count_jsonl(root / "gold" / GOLD_METRICS)
    status = backend_status()
    return {
        "backend": status["backend"],
        "databricks_configured": status["databricks_configured"],
        "databricks_called": False,
        "note": status["note"],
        "bronze": bronze,
        "silver": silver,
        "gold": working_set + bundles + metrics,
        "gold_detail": {
            "working_set": working_set,
            "bundles": bundles,
            "metrics": metrics,
        },
        "paths": {
            "bronze": str(root / "bronze" / BRONZE_NAME),
            "silver": str(root / "silver" / SILVER_NAME),
            "working_set": str(root / "gold" / GOLD_WORKING_SET),
            "bundles": str(root / "gold" / GOLD_BUNDLES),
            "metrics": str(root / "gold" / GOLD_METRICS),
        },
    }


def _bronze_row(event: dict) -> dict:
    return {
        "event_id": event["event_id"],
        "session_id": event["session_id"],
        "task_id": event.get("task_id"),
        "turn": event.get("turn"),
        "timestamp_ms": event.get("timestamp_ms"),
        "source": event.get("source"),
        "operation": event.get("operation"),
        "query": event.get("query"),
        "regions": list(event.get("regions") or []),
        "latency_ms": event.get("latency_ms"),
        "bytes_returned": event.get("bytes_returned"),
        "tokens_returned": event.get("tokens_returned"),
        "success": bool(event.get("success")),
        "extra": event.get("extra") or {},
    }


def export_bronze(conn, path: Path) -> dict[str, Any]:
    """Append events that are not already in the bronze file.

    Lines already on disk are copied through unchanged. The ``events`` table
    is only read.
    """
    existing_lines: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            eid = row.get("event_id")
            if eid and eid not in existing_lines:
                existing_lines[eid] = line

    db_rows = conn.execute(
        "SELECT * FROM events ORDER BY timestamp_ms ASC, event_id ASC"
    ).fetchall()
    ordered: list[str] = []
    seen: set[str] = set()
    appended = 0
    for raw in db_rows:
        event = event_from_row(raw)
        eid = event["event_id"]
        seen.add(eid)
        if eid in existing_lines:
            ordered.append(existing_lines[eid])
            continue
        ordered.append(json.dumps(_bronze_row(event), sort_keys=True, default=str))
        appended += 1
    for eid, line in existing_lines.items():
        if eid not in seen:
            ordered.append(line)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(("\n".join(ordered) + ("\n" if ordered else "")), encoding="utf-8")
    return {"path": str(path), "rows": len(ordered), "appended": appended}


def _traced_region_ids(events: list[dict]) -> set[str]:
    found: set[str] = set()
    for event in events:
        if event.get("source") != "program_trace":
            continue
        for region_id in event.get("regions") or []:
            found.add(region_id)
    return found


def silver_observations(conn, cfg: TraceWeaverConfig) -> list[dict]:
    """Inner-join agent_tool region hits with program_trace regions.

    Each row is one agent event touching one source region that a program
    trace also observed. Queries are passed through the redactor. Secret
    paths are dropped.
    """
    events = [
        event_from_row(row)
        for row in conn.execute("SELECT * FROM events ORDER BY timestamp_ms ASC, event_id ASC")
    ]
    traced = _traced_region_ids(events)
    if not traced:
        return []
    out: list[dict] = []
    for event in events:
        if event.get("source") != "agent_tool":
            continue
        for region_id in event.get("regions") or []:
            if region_id not in traced:
                continue
            region = conn.execute(
                """
                SELECT path, symbol, content_hash, commit_sha, kind
                FROM source_regions WHERE region_id = ?
                """,
                (region_id,),
            ).fetchone()
            if region is None:
                continue
            path = region["path"] or ""
            if should_redact_path(path, cfg):
                continue
            out.append(
                {
                    "event_id": event["event_id"],
                    "session_id": event["session_id"],
                    "task_id": event.get("task_id"),
                    "timestamp_ms": event.get("timestamp_ms"),
                    "operation": event.get("operation"),
                    "region_id": region_id,
                    "path": path,
                    "symbol": region["symbol"],
                    "content_hash": region["content_hash"],
                    "commit": region["commit_sha"],
                    "kind": region["kind"],
                    "query": redact_text(event.get("query") or ""),
                    "program_traced": True,
                }
            )
    return out


def _pct(baseline: float | None, traceweaver: float | None) -> float | None:
    if baseline is None or traceweaver is None:
        return None
    try:
        base = float(baseline)
        led = float(traceweaver)
    except (TypeError, ValueError):
        return None
    if base == 0:
        return None
    return round((led - base) / base * 100.0, 1)


def _metric_row(data: dict, source_file: str, kind: str) -> dict | None:
    baseline = data.get("baseline") or {}
    traceweaver = data.get("traceweaver") or {}
    if not isinstance(baseline, dict) or not isinstance(traceweaver, dict):
        return None
    base_calls = baseline.get("repo_tool_calls")
    led_calls = traceweaver.get("repo_tool_calls")
    base_tokens = baseline.get("repo_tokens")
    led_tokens = traceweaver.get("repo_tokens")
    if base_calls is None and base_tokens is None:
        return None
    return {
        "kind": kind,
        "source_file": source_file,
        "fixture": bool(data.get("fixture")),
        "task_id": data.get("task_id"),
        "agent": data.get("agent"),
        "baseline_repo_calls": base_calls,
        "traceweaver_repo_calls": led_calls,
        "call_change_pct": _pct(base_calls, led_calls),
        "baseline_repo_tokens": base_tokens,
        "traceweaver_repo_tokens": led_tokens,
        "token_change_pct": _pct(base_tokens, led_tokens),
        "materialized_ms": now_ms(),
        "backend": "local-lake",
    }


def _load_report(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("fixture") is True:
        return None
    return data


def savings_from_latest(cfg: TraceWeaverConfig) -> dict | None:
    """Token and call deltas from the newer of last_eval.json and last_ab.json."""
    candidates: list[tuple[float, str, dict]] = []
    for name, kind in (("last_eval.json", "eval"), ("last_ab.json", "ab")):
        path = cfg.traceweaver_dir / name
        data = _load_report(path)
        if data is None:
            continue
        row = _metric_row(data, name, kind)
        if row is None:
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        candidates.append((mtime, name, row))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][2]


def _audit_row(audit: dict) -> dict:
    cut = audit.get("min_cut") or {}
    bfs = audit.get("reverse_bfs") or {}
    seed_side = list(cut.get("seed_side") or [])
    return {
        "kind": "audit",
        "source_file": "index",
        "fixture": False,
        "backend": "local-lake",
        "largest_scc_size": audit.get("largest_scc_size"),
        "large_sccs": audit.get("large_sccs") or [],
        "reverse_bfs_depth": bfs.get("depth"),
        "reverse_bfs_reaches_failing_test": bfs.get("reaches_failing_test"),
        "reverse_bfs_path": bfs.get("path") or [],
        "reverse_bfs_seed": bfs.get("seed"),
        "failing_test": bfs.get("failing_test"),
        "min_cut_weight": cut.get("weight"),
        "min_cut_nodes": cut.get("nodes"),
        "min_cut_seed_side": seed_side,
        "min_cut_seed_side_count": len(seed_side),
        "min_cut_other_side": list(cut.get("other_side") or []),
        "materialized_ms": now_ms(),
    }


def _working_set_rows(conn, cfg: TraceWeaverConfig) -> list[dict]:
    rows = conn.execute(
        """
        SELECT w.session_id, w.region_id, w.admitted, w.pinned, w.stale,
               w.execution_score, w.agent_access_score, w.token_cost, w.explanation,
               r.path, r.symbol, r.content_hash, r.commit_sha
        FROM working_set w
        LEFT JOIN source_regions r ON r.region_id = w.region_id
        ORDER BY w.session_id, w.execution_score DESC
        """
    ).fetchall()
    out = []
    for row in rows:
        path = row["path"] or ""
        if path and should_redact_path(path, cfg):
            continue
        out.append(
            {
                "session_id": row["session_id"],
                "region_id": row["region_id"],
                "path": path or None,
                "symbol": row["symbol"],
                "content_hash": row["content_hash"],
                "commit": row["commit_sha"],
                "admitted": bool(row["admitted"]),
                "pinned": bool(row["pinned"]),
                "stale": bool(row["stale"]),
                "execution_score": row["execution_score"],
                "agent_access_score": row["agent_access_score"],
                "token_cost": row["token_cost"],
                "explanation": redact_text((row["explanation"] or "")[:240]),
                "materialized_ms": now_ms(),
            }
        )
    return out


def _latest_bundle_row(conn) -> list[dict]:
    row = conn.execute(
        "SELECT * FROM bundles ORDER BY created_ms DESC LIMIT 1"
    ).fetchone()
    if row is None:
        return []
    try:
        payload = json.loads(row["payload_json"] or "{}")
    except json.JSONDecodeError:
        payload = {}
    entries = []
    for entry in payload.get("entries") or []:
        path = entry.get("path") or ""
        entries.append(
            {
                "region_id": entry.get("region_id"),
                "path": path,
                "symbol": entry.get("symbol"),
                "content_hash": entry.get("content_hash"),
                "start_line": entry.get("start_line"),
                "end_line": entry.get("end_line"),
            }
        )
    return [
        {
            "bundle_id": row["bundle_id"],
            "session_id": row["session_id"],
            "objective": redact_text(row["objective"] or ""),
            "budget": row["budget"],
            "created_ms": row["created_ms"],
            "token_count": payload.get("token_count"),
            "latest": True,
            "fixture": False,
            "entries": entries,
            "materialized_ms": now_ms(),
        }
    ]


def _metric_rows(cfg: TraceWeaverConfig, audit: dict) -> list[dict]:
    rows: list[dict] = []
    for name, kind in (("last_ab.json", "ab"), ("last_eval.json", "eval")):
        data = _load_report(cfg.traceweaver_dir / name)
        if data is None:
            continue
        row = _metric_row(data, name, kind)
        if row is not None:
            rows.append(row)
    rows.append(_audit_row(audit))
    return rows


def build_lake(cfg: TraceWeaverConfig) -> dict[str, Any]:
    """Read the event log and write bronze, silver, and gold JSONL files."""
    from traceweaver.audit import build_audit
    from traceweaver.runtime import TraceWeaverRuntime

    cfg.ensure_dirs()
    status = backend_status()
    root = lake_root(cfg)
    rt = TraceWeaverRuntime(cfg)
    try:
        rt.sync()
        bronze = export_bronze(rt.conn, root / "bronze" / BRONZE_NAME)
        silver_rows = silver_observations(rt.conn, cfg)
        silver_path = root / "silver" / SILVER_NAME
        _write_jsonl(silver_path, silver_rows)
        working_rows = _working_set_rows(rt.conn, cfg)
        bundle_rows = _latest_bundle_row(rt.conn)
        audit = build_audit(cfg)
        metric_rows = _metric_rows(cfg, audit)
        gold = root / "gold"
        _write_jsonl(gold / GOLD_WORKING_SET, working_rows)
        _write_jsonl(gold / GOLD_BUNDLES, bundle_rows)
        _write_jsonl(gold / GOLD_METRICS, metric_rows)
    finally:
        rt.conn.close()

    summary = lake_summary(cfg)
    manifest = {
        "backend": "local-lake",
        "databricks_called": False,
        "note": status["note"],
        "built_ms": now_ms(),
        "counts": {
            "bronze": summary["bronze"],
            "silver": summary["silver"],
            "gold": summary["gold_detail"],
        },
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    savings = savings_from_latest(cfg)
    return {
        "backend": "local-lake",
        "databricks_called": False,
        "note": status["note"],
        "bronze": bronze,
        "silver": {"path": str(root / "silver" / SILVER_NAME), "rows": len(silver_rows)},
        "gold": {
            "working_set": {"path": str(root / "gold" / GOLD_WORKING_SET), "rows": len(working_rows)},
            "bundles": {"path": str(root / "gold" / GOLD_BUNDLES), "rows": len(bundle_rows)},
            "metrics": {"path": str(root / "gold" / GOLD_METRICS), "rows": len(metric_rows)},
        },
        "savings": savings,
        "summary": summary,
    }


def format_lake_report(report: dict) -> str:
    lines = [
        "backend: local-lake",
        report.get("note") or "",
        f"bronze  events.jsonl  {report['bronze']['rows']}",
        f"silver  region_observations.jsonl  {report['silver']['rows']}",
        f"gold    working_set.jsonl  {report['gold']['working_set']['rows']}",
        f"gold    bundles.jsonl  {report['gold']['bundles']['rows']}",
        f"gold    metrics.jsonl  {report['gold']['metrics']['rows']}",
        format_savings(report.get("savings")),
    ]
    return "\n".join(line for line in lines if line is not None)


def format_savings(savings: dict | None) -> str:
    if not savings:
        return "savings: no last_eval.json or last_ab.json"
    def pct(value: float | None) -> str:
        return "—" if value is None else f"{value:+.1f}%"

    return (
        f"savings source: {savings['source_file']}\n"
        f"  repo calls: {savings.get('baseline_repo_calls')} → {savings.get('traceweaver_repo_calls')} "
        f"({pct(savings.get('call_change_pct'))})\n"
        f"  repo tokens: {savings.get('baseline_repo_tokens')} → {savings.get('traceweaver_repo_tokens')} "
        f"({pct(savings.get('token_change_pct'))})"
    )


def format_show(cfg: TraceWeaverConfig) -> str:
    summary = lake_summary(cfg)
    detail = summary["gold_detail"]
    lines = [
        "backend: local-lake",
        summary["note"],
        f"bronze  events.jsonl  {summary['bronze']}",
        f"silver  region_observations.jsonl  {summary['silver']}",
        f"gold    working_set.jsonl  {detail['working_set']}",
        f"gold    bundles.jsonl  {detail['bundles']}",
        f"gold    metrics.jsonl  {detail['metrics']}",
        format_savings(savings_from_latest(cfg)),
    ]
    return "\n".join(lines)
