"""A/B evaluation: same task, same repo commit, same policy — with and without LEDGER.

Fair-run rules enforced here (spec §14.6):
  * clean working tree between conditions (target files snapshotted/restored, index rebuilt)
  * each condition gets a fresh session (no shared working set) unless ``warm=True``
  * LEDGER's own injected tokens and calls are counted against LEDGER
  * success is reported before efficiency; held-out tasks are kept separate
"""

from __future__ import annotations

import json
import statistics
import time
from pathlib import Path

import yaml

from ledger.adapters.harness import SimulatedAgent
from ledger.config import LedgerConfig
from ledger.eval.metrics import aggregate, per_task_rows
from ledger.runtime import LedgerRuntime


def tasks_path(repo_root: Path) -> Path:
    local = repo_root / "ledger.eval.yaml"
    return local if local.exists() else Path(__file__).with_name("tasks.yaml")


def load_tasks(repo_root: Path) -> list[dict]:
    data = yaml.safe_load(tasks_path(repo_root).read_text()) or {}
    return data.get("tasks") or data


def snapshot_targets(repo_root: Path, task: dict) -> dict[str, str]:
    files = set(task.get("target_files") or [])
    if task.get("patch"):
        files.add(task["patch"]["path"])
    return {rel: (repo_root / rel).read_text(encoding="utf-8") for rel in files if (repo_root / rel).exists()}


def restore_snapshot(repo_root: Path, snap: dict[str, str]) -> None:
    for rel, text in snap.items():
        (repo_root / rel).write_text(text, encoding="utf-8")


def run_task(runtime: LedgerRuntime, task: dict, use_ledger: bool) -> dict:
    snap = snapshot_targets(runtime.cfg.repo_root, task)
    try:
        return SimulatedAgent(runtime, use_ledger=use_ledger).run(task)
    finally:
        restore_snapshot(runtime.cfg.repo_root, snap)
        runtime.sync()  # incremental reindex of anything the run touched


def compare_task(cfg: LedgerConfig, task: dict, warm: bool = False) -> dict:
    base_rt = LedgerRuntime(cfg)
    base_rt.new_session(agent="simulated", condition="baseline", task_id=task["id"])
    base = run_task(base_rt, task, use_ledger=False)

    led_rt = LedgerRuntime(cfg)
    led_rt.new_session(agent="simulated", condition="ledger", task_id=task["id"])
    led = run_task(led_rt, task, use_ledger=True)
    return _compare_rows(task, base, led)


def _compare_rows(task: dict, base: dict, led: dict) -> dict:
    def delta(key):
        b, l = base.get(key), led.get(key)
        if isinstance(b, (int, float)) and isinstance(l, (int, float)) and b:
            return round((l - b) / b * 100, 1)
        return None

    return {
        "task_id": task["id"],
        "family": task.get("family"),
        "held_out": bool(task.get("held_out")),
        "baseline": base,
        "ledger": led,
        "change_pct": {k: delta(k) for k in ("repo_tool_calls", "total_tool_calls", "repo_tokens", "time_s", "time_to_target_s", "calls_to_target")},
        "success_held": bool(base.get("success")) == bool(led.get("success")) or bool(led.get("success")),
        "regression": bool(base.get("success")) and not bool(led.get("success")),
    }


def run_suite(cfg: LedgerConfig, held_out: bool | None = False, repeats: int = 1, task_ids: list[str] | None = None) -> dict:
    """held_out=False → dev tasks; True → held-out only; None → all."""
    tasks = load_tasks(cfg.repo_root)
    if task_ids:
        tasks = [t for t in tasks if t["id"] in set(task_ids)]
    if held_out is not None:
        tasks = [t for t in tasks if bool(t.get("held_out")) == held_out]
    rows = []
    t0 = time.time()
    for rep in range(repeats):
        for task in tasks:
            row = compare_task(cfg, task)
            row["repeat"] = rep
            rows.append(row)
    suite = {
        "generated_ms": int(time.time() * 1000),
        "repo": str(cfg.repo_root),
        "agent": "simulated",
        "repeats": repeats,
        "held_out": held_out,
        "n_tasks": len(tasks),
        "elapsed_s": round(time.time() - t0, 1),
        "tasks": rows,
        "per_task": per_task_rows(rows),
        "baseline": aggregate([r["baseline"] for r in rows]),
        "ledger": aggregate([r["ledger"] for r in rows]),
        "config": cfg.to_json(),
    }
    suite["change_pct"] = _suite_change(suite["baseline"], suite["ledger"])
    suite["regressions"] = [r["task_id"] for r in rows if r["regression"]]
    cfg.ledger_dir.mkdir(parents=True, exist_ok=True)
    (cfg.ledger_dir / "last_eval.json").write_text(json.dumps(suite, indent=2, default=str))
    (cfg.ledger_dir / "last_eval.md").write_text(render_markdown(suite))
    return suite


def _suite_change(b: dict, l: dict) -> dict:
    out = {}
    for k in ("repo_tool_calls", "total_tool_calls", "repo_tokens", "median_time_to_target_s", "mean_calls_to_target"):
        bv, lv = b.get(k), l.get(k)
        out[k] = round((lv - bv) / bv * 100, 1) if isinstance(bv, (int, float)) and isinstance(lv, (int, float)) and bv else None
    return out


def render_markdown(suite: dict) -> str:
    b, l, c = suite["baseline"], suite["ledger"], suite["change_pct"]
    lines = [
        f"# LEDGER vs baseline — {suite['n_tasks']} tasks × {suite['repeats']} repeat(s) ({'held-out' if suite['held_out'] else 'dev' if suite['held_out'] is False else 'all'})",
        "",
        "Same simulated agent policy, same repository commit, same tests. LEDGER's injected tokens and context calls are counted against LEDGER.",
        "",
        "| Condition | Success | Repo search/read calls | Total tool calls (incl. LEDGER) | Repo tokens (incl. injected) | Median time to target | Mean calls to target |",
        "|---|---:|---:|---:|---:|---:|---:|",
        f"| Baseline | {b['success']}/{b['n']} | {b['repo_tool_calls']} | {b['total_tool_calls']} | {b['repo_tokens']:,} | {b['median_time_to_target_s']} s | {b['mean_calls_to_target']} |",
        f"| LEDGER | {l['success']}/{l['n']} | {l['repo_tool_calls']} | {l['total_tool_calls']} | {l['repo_tokens']:,} | {l['median_time_to_target_s']} s | {l['mean_calls_to_target']} |",
        f"| Change | {'equal' if b['success'] == l['success'] else 'DIFF'} | {_pct(c['repo_tool_calls'])} | {_pct(c['total_tool_calls'])} | {_pct(c['repo_tokens'])} | {_pct(c['median_time_to_target_s'])} | {_pct(c['mean_calls_to_target'])} |",
        "",
        f"Controller: prefetch precision {l.get('prefetch_precision')} · pollution rate {l.get('pollution_rate')} · cache hit rate {l.get('hit_rate')} · stale served {l.get('stale_served')} · fallback rate {l.get('fallback_rate')}",
        "",
        "## Per task",
        "",
        "| Task | Family | Base ✓ | LEDGER ✓ | Base calls | LEDGER calls | Base tokens | LEDGER tokens | Δ tokens |",
        "|---|---|:-:|:-:|---:|---:|---:|---:|---:|",
    ]
    for r in suite["per_task"]:
        lines.append(
            f"| {r['task_id']} | {r['family']} | {'✓' if r['baseline_success'] else '✗'} | {'✓' if r['ledger_success'] else '✗'} | {r['baseline_calls']} | {r['ledger_calls']} | {r['baseline_tokens']:,} | {r['ledger_tokens']:,} | {_pct(r['tokens_change_pct'])} |"
        )
    if suite["regressions"]:
        lines += ["", f"**Regressions (baseline passed, LEDGER failed):** {', '.join(suite['regressions'])}"]
    return "\n".join(lines) + "\n"


def _pct(v) -> str:
    return "—" if v is None else f"{v:+.1f}%"
