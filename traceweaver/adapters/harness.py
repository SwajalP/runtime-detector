"""Deterministic simulated agent for A/B evaluation without a model API.

The simulated agent is a *fixed exploration policy*, identical across the two
conditions except for the tools it is given:

  baseline  : run the targeted test, then for each query term -> grep the repo
              and read every unread matching file (up to ``max_reads_per_grep``).
  traceweaver    : run the targeted test (traced), call ``traceweaver_context`` once, read
              the bundle, then for each query term -> ``traceweaver_search`` and read
              at most ``max_reads_per_search`` unread files it ranks highly.

Neither condition is told which files are the ground-truth targets; success is
scored afterwards from the observed trajectory. Token costs of TraceWeaver's own
injected bundles/search results are counted against the TraceWeaver condition.
"""

from __future__ import annotations

import re
import time
from pathlib import Path

from traceweaver.events.schema import estimate_tokens
from traceweaver.index.lexical import grep_regions
from traceweaver.index.symbols import regions_for_path, regions_in_range
from traceweaver.policy.invalidation import invalidate_path
from traceweaver.trace.runner import run_pytest_traced

MAX_READS_PER_GREP = 6
MAX_READS_PER_SEARCH = 2
SEARCH_READ_THRESHOLD = 0.45


class SimulatedAgent:
    def __init__(self, runtime, use_traceweaver: bool) -> None:
        self.runtime = runtime
        self.use_traceweaver = use_traceweaver
        self.read_files: set[str] = set()

    # ---------------------------------------------------------------- driver

    def run(self, task: dict) -> dict:
        rt = self.runtime
        session_id = rt.new_session(
            agent="simulated", condition="traceweaver" if self.use_traceweaver else "baseline", task_id=task["id"]
        )
        rt.set_objective(session_id, task["prompt"])
        started = time.time()
        targets = set(task.get("target_files") or [])
        observed: set[str] = set()
        first_target: dict[str, float | None] = {"t": None, "calls": None}
        calls = {"n": 0}

        def note(paths: list[str]) -> None:
            for p in paths:
                if p in targets and p not in observed:
                    observed.add(p)
                    if first_target["t"] is None:
                        first_target["t"] = time.time() - started
                        first_target["calls"] = calls["n"]

        queries = task.get("baseline_queries") or _keywords(task["prompt"])

        # Both conditions start by running the targeted test (what a real agent does first).
        test = self._run_tests(session_id, task)

        if self.use_traceweaver:
            bundle = rt.controller.build_bundle(
                session_id, objective=task["prompt"], seed=task.get("seed"), task_id=task["id"]
            )
            calls["n"] += 1
            covered: set[str] = set()
            for entry in bundle["entries"]:
                self._consume_bundle_entry(session_id, entry, task)
                note([entry["path"]])
                if entry["level"] == "exact":
                    covered.add(entry["path"])  # exact code in context: no need to re-open the file
            for q in queries:
                res = rt.controller.search(session_id, q)
                calls["n"] += 1
                reads = 0
                for r in res["regions"]:
                    if r["score"] < SEARCH_READ_THRESHOLD or r["path"] in self.read_files or r["path"] in covered:
                        continue
                    self._read_file(session_id, r["path"], task, calls)
                    note([r["path"]])
                    reads += 1
                    if reads >= MAX_READS_PER_SEARCH:
                        break
        else:
            for q in queries:
                files = self._grep(session_id, q, task, calls)
                note(files)
                reads = 0
                for rel in files:
                    if rel in self.read_files:
                        continue
                    self._read_file(session_id, rel, task, calls)
                    note([rel])
                    reads += 1
                    if reads >= MAX_READS_PER_GREP:
                        break

        patched = self._maybe_edit(session_id, task, observed, calls)
        success = self._success(task, observed, patched, session_id)
        ended = time.time()
        rt.collector.record(
            session_id=session_id, source="agent_tool", operation="complete", task_id=task["id"],
            success=success, extra={"observed_targets": sorted(observed), "patched": patched},
        )
        rt.end_session(session_id, success)
        metrics = rt.controller.metrics(session_id)
        metrics.update(
            {
                "task_id": task["id"],
                "family": task.get("family"),
                "held_out": bool(task.get("held_out")),
                "condition": "traceweaver" if self.use_traceweaver else "baseline",
                "success": success,
                "patched": patched,
                "time_s": round(ended - started, 3),
                "time_to_target_s": round(first_target["t"], 3) if first_target["t"] is not None else None,
                "calls_to_target": first_target["calls"],
                "files_read": len(self.read_files),
                "observed_targets": sorted(observed),
                "localized": bool(observed),
                "initial_test_passed": test.get("passed"),
            }
        )
        return metrics

    # ----------------------------------------------------------------- tools

    def _grep(self, session_id: str, term: str, task: dict, calls: dict) -> list[str]:
        """Model of an agent Grep: regex over the repo, returning matching files + lines."""
        rt = self.runtime
        t0 = time.time()
        rx = re.compile(re.escape(term), re.I)
        root = rt.cfg.repo_root
        files, out_lines = [], []
        for path in sorted(root.rglob("*")):
            if not path.is_file() or rt.cfg.is_excluded(path) or path.suffix not in {".py", ".md", ".txt", ".toml"}:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hit = False
            for i, line in enumerate(text.splitlines(), 1):
                if rx.search(line):
                    out_lines.append(f"{path.relative_to(root)}:{i}:{line.strip()[:160]}")
                    hit = True
            if hit:
                files.append(str(path.relative_to(root)))
        payload = "\n".join(out_lines)
        ids = [r["region_id"] for r in grep_regions(rt.conn, re.escape(term), 24)]
        calls["n"] += 1
        rt.collector.record(
            session_id=session_id, source="agent_tool", operation="grep", task_id=task["id"], query=term,
            regions=ids, latency_ms=int((time.time() - t0) * 1000),
            tokens_returned=estimate_tokens(payload), bytes_returned=len(payload.encode()),
            extra={"files": len(files), "lines": len(out_lines)}, bump=True,
        )
        rt.controller.observe_agent_regions(session_id, ids)
        # agents open .py files; docs appear in results but are read less often
        return [f for f in files if f.endswith(".py")] + [f for f in files if not f.endswith(".py")][:1]

    def _read_file(self, session_id: str, rel: str, task: dict, calls: dict) -> None:
        rt = self.runtime
        path = rt.cfg.repo_root / rel
        if not path.exists():
            return
        self.read_files.add(rel)
        text = path.read_text(encoding="utf-8", errors="replace")
        ids = [r["region_id"] for r in regions_for_path(rt.conn, rel) if r["kind"] != "module"]
        calls["n"] += 1
        rt.collector.record(
            session_id=session_id, source="agent_tool", operation="read", task_id=task["id"], query=rel,
            regions=ids, tokens_returned=estimate_tokens(text), bytes_returned=len(text.encode()), bump=True,
        )
        rt.controller.observe_agent_regions(session_id, ids)

    def _consume_bundle_entry(self, session_id: str, entry: dict, task: dict) -> None:
        """The agent consumes a bundle entry: a cache hit, not a repository tool call.

        Tokens were charged once on ``bundle_served``; this only updates the
        working set so later ranking/eviction sees the access.
        """
        self.runtime.controller.observe_agent_regions(session_id, [entry["region_id"]])

    def _run_tests(self, session_id: str, task: dict) -> dict:
        rt = self.runtime
        args = task.get("pytest_args") or ["-q"]
        if self.use_traceweaver:
            return run_pytest_traced(
                rt.cfg, session_id, args=args, conn=rt.conn, collector=rt.collector,
                controller=rt.controller, task_id=task["id"],
            )
        # Baseline: the agent runs pytest and only sees its textual output.
        result = run_pytest_traced(rt.cfg, session_id, args=args, conn=None, collector=None, controller=None, record_event=False)
        output = result["stdout"] + result["stderr"]
        rt.collector.record(
            session_id=session_id, source="agent_tool", operation="execute", task_id=task["id"],
            query="pytest " + " ".join(args), tokens_returned=estimate_tokens(output),
            bytes_returned=len(output.encode()), success=result["passed"], bump=True,
        )
        return result

    def _maybe_edit(self, session_id: str, task: dict, observed: set[str], calls: dict) -> bool:
        patch = task.get("patch")
        if not patch or patch["path"] not in observed:
            return False
        rt = self.runtime
        rel = patch["path"]
        path = rt.cfg.repo_root / rel
        text = path.read_text(encoding="utf-8")
        if patch["find"] not in text:
            return False
        new_text = text.replace(patch["find"], patch["replace"], 1)
        # locate edited line range for region attribution
        before = text[: text.index(patch["find"])].count("\n") + 1
        after = before + patch["find"].count("\n")
        ids = [r["region_id"] for r in regions_in_range(rt.conn, rel, before, after)]
        path.write_text(new_text, encoding="utf-8")
        calls["n"] += 1
        rt.collector.record(
            session_id=session_id, source="edit", operation="edit", task_id=task["id"], query=rel,
            regions=ids, extra={"lines": [before, after]}, bump=True,
        )
        invalidate_path(rt.conn, rt.cfg, session_id, rel, rt.collector)
        rt.controller.observe_edit(session_id, [r["region_id"] for r in regions_in_range(rt.conn, rel, before, after)])
        return True

    def _success(self, task: dict, observed: set[str], patched: bool, session_id: str) -> bool:
        mode = task.get("success", "localize")
        targets = set(task.get("target_files") or [])
        if mode == "patch":
            if not patched:
                return False
            result = run_pytest_traced(
                self.runtime.cfg, session_id, args=task.get("pytest_args") or ["-q"],
                conn=self.runtime.conn, collector=self.runtime.collector, controller=self.runtime.controller,
                task_id=task["id"],
            )
            return bool(result["passed"])
        return bool(targets & observed) if targets else bool(observed)


def _keywords(prompt: str) -> list[str]:
    stop = {"the", "and", "when", "with", "from", "that", "this", "into", "for", "find", "locate", "which", "where"}
    words = re.findall(r"[A-Za-z_]{4,}", prompt.lower())
    out: list[str] = []
    for w in words:
        if w not in stop and w not in out:
            out.append(w)
    return out[:5] or [prompt[:40]]
