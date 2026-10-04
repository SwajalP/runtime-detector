"""Framework-free glue between the Agentverse models and the LEDGER controller.

Everything here is synchronous and importable without ``uagents`` networking,
so it can be unit-tested directly. The uAgent in :mod:`ledger.agentverse.agent`
runs these calls on a dedicated worker thread (the sqlite connection owned by
:class:`LedgerRuntime` is not shared across threads).

Security posture: inbound text is *data*. Objectives and queries are handed to
the controller's lexical/structural scorer; pytest arguments are whitelisted
to repo-relative test paths and a few harmless flags; nothing is ever passed
to a shell or ``eval``.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from ledger.agentverse.models import (
    ContextBundle,
    ContextRequest,
    ErrorResponse,
    ExplainRequest,
    ExplainResponse,
    MetricsRequest,
    MetricsResponse,
    PrefetchEntry,
    RegionEntry,
    RejectedEntry,
    SearchRegion,
    SearchRequest,
    SearchResponse,
    TraceFrame,
    TraceRegion,
    TraceRequest,
    TraceResponse,
)
from ledger.config import LedgerConfig
from ledger.runtime import LedgerRuntime

MAX_TEXT = 4000  # hard cap on any inbound free-text field
MAX_BUDGET = 12000
MIN_BUDGET = 200

_BUNDLE_ID_RX = re.compile(r"\bbnd[_-][A-Za-z0-9_-]{4,}\b")
_BACKTICK_RX = re.compile(r"`([^`\s]{2,80})`")
_IDENT_RX = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b")
_TEST_PATH_RX = re.compile(r"^[A-Za-z0-9_./-]+\.py(::[A-Za-z0-9_\[\]\-,.]+)*$")
_SAFE_PYTEST_FLAGS = {"-q", "-x", "--tb=short", "--tb=line", "--lf", "--ff", "-rA"}


# ------------------------------------------------------------------- helpers


def _clip(s: str | None, n: int = MAX_TEXT) -> str:
    return (s or "")[:n]


def _budget(v: int | None, default: int) -> int:
    if v is None:
        return default
    try:
        v = int(v)
    except (TypeError, ValueError):
        return default
    return max(MIN_BUDGET, min(MAX_BUDGET, v))


def sanitize_pytest_args(args: list[str] | None, repo_root: Path) -> list[str]:
    """Keep only repo-relative ``*.py[::node]`` paths and a few safe flags."""
    out: list[str] = []
    for raw in args or []:
        a = str(raw).strip()
        if not a:
            continue
        if a in _SAFE_PYTEST_FLAGS:
            out.append(a)
            continue
        if a.startswith("-"):
            continue  # unknown flag: drop
        if not _TEST_PATH_RX.match(a) or ".." in a or a.startswith("/"):
            continue
        file_part = a.split("::", 1)[0]
        try:
            resolved = (repo_root / file_part).resolve()
            resolved.relative_to(repo_root.resolve())
        except (ValueError, OSError):
            continue
        if resolved.exists():
            out.append(a)
    if "-q" not in out:
        out.insert(0, "-q")
    return out


# ------------------------------------------------------------------- intents


class Intent:
    CONTEXT = "context"
    SEARCH = "search"
    EXPLAIN = "explain"
    TRACE = "trace"
    METRICS = "metrics"
    HELP = "help"


def parse_intent(text: str) -> tuple[str, dict]:
    """Deterministic keyword routing for Chat Protocol messages.

    Returns ``(intent, params)`` where params already carry the typed fields
    for the matching request model. Default intent is ``context``.
    """
    raw = _clip(text).strip()
    lowered = raw.lower()
    first = lowered.split(maxsplit=1)[0] if lowered else ""

    if not raw or first in {"help", "?", "/help", "hi", "hello"} and len(lowered.split()) <= 3:
        return Intent.HELP, {}

    m = _BUNDLE_ID_RX.search(raw)
    if first in {"explain", "/explain", "why"} or (m and "explain" in lowered):
        return Intent.EXPLAIN, {"bundle_id": m.group(0) if m else ""}

    if first in {"metrics", "/metrics", "stats", "/stats"} or lowered.startswith("show metrics"):
        return Intent.METRICS, {}

    if first in {"trace", "/trace", "pytest", "/pytest"} or lowered.startswith(("run test", "run the test", "run pytest")):
        tokens = raw.split()
        return Intent.TRACE, {"pytest_args": [t for t in tokens[1:] if t.lower() not in {"tests", "test", "the", "run"}] if tokens else []}

    if first in {"search", "/search", "grep", "/grep", "lookup"}:
        q = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""
        return Intent.SEARCH, {"query": q}

    if first in {"context", "/context"}:
        raw = raw.split(maxsplit=1)[1] if len(raw.split(maxsplit=1)) > 1 else ""

    seed = None
    bt = _BACKTICK_RX.search(raw)
    if bt:
        seed = bt.group(1)
    return Intent.CONTEXT, {"objective": raw, "seed": seed}


HELP_TEXT = (
    "**LEDGER Runtime** — budgeted, explainable repository context for coding agents.\n\n"
    "Tell me what you are trying to fix or find and I will return the source regions most "
    "likely to matter, under a token budget, with a reason for each.\n\n"
    "Commands (plain text works too):\n"
    "- `<objective>` — e.g. *find the code responsible for renewal invoices ignoring loyalty discounts*\n"
    "- `search <query>` — lexical+structural region search (signatures only)\n"
    "- `trace tests/test_renewal_discount.py` — run pytest under coverage; which regions executed / failed\n"
    "- `explain <bundle_id>` — per-region score breakdown for a served bundle\n"
    "- `metrics` — hit/miss, prefetch, eviction, invalidation counters for your session\n"
)


# ------------------------------------------------------------------- service


class LedgerService:
    """Thin, synchronous façade over :class:`LedgerRuntime` for agent handlers."""

    def __init__(self, repo: Path | str | None = None, runtime: LedgerRuntime | None = None) -> None:
        if runtime is not None:
            self.runtime = runtime
        else:
            cfg = LedgerConfig.from_root(Path(repo)) if repo else LedgerConfig.discover()
            self.runtime = LedgerRuntime(cfg)
        self.cfg = self.runtime.cfg
        self._indexed = False

    # ----------------------------------------------------------- lifecycle

    def ensure_index(self) -> dict:
        """Index the repo once if the DB is empty; otherwise sync changed files."""
        n = self.runtime.conn.execute("SELECT COUNT(*) c FROM source_regions").fetchone()["c"]
        if n == 0:
            stats = self.runtime.reindex()
        else:
            stats = self.runtime.sync()
        self._indexed = True
        return stats

    def session_for(self, requester: str | None, task_id: str | None = None) -> str:
        """One LEDGER session per requesting agent address (isolated working sets)."""
        if not self._indexed:
            self.ensure_index()
        return self.runtime.ensure_session(
            agent="agentverse", condition="ledger", task_id=task_id, external_id=requester or None
        )

    @property
    def repo_name(self) -> str:
        return self.cfg.repo_root.name

    # ------------------------------------------------------------- actions

    def context(self, req: ContextRequest, requester: str | None = None) -> ContextBundle | ErrorResponse:
        objective = _clip(req.objective).strip()
        if not objective:
            return ErrorResponse(error="empty objective", request_type="ContextRequest")
        if req.repo and req.repo not in {self.repo_name, str(self.cfg.repo_root)}:
            return ErrorResponse(
                error="repo mismatch",
                detail=f"this agent serves '{self.repo_name}', not '{_clip(req.repo, 120)}'",
                request_type="ContextRequest",
            )
        sid = self.session_for(requester, req.task_id)
        self.runtime.set_objective(sid, objective)
        self.runtime.sync()
        payload = self.runtime.controller.build_bundle(
            sid,
            objective,
            seed=_clip(req.seed, 200) or None,
            budget=_budget(req.budget, self.cfg.token_budget),
            task_id=req.task_id,
        )
        explanation = self.runtime.controller.explain(payload["bundle_id"]).get("text", "")
        bundle = ContextBundle(
            bundle_id=payload["bundle_id"],
            session_id=sid,
            objective=objective,
            repo=self.repo_name,
            token_count=int(payload["token_count"]),
            budget=int(payload["budget"]),
            entries=[
                RegionEntry(
                    region_id=e["region_id"],
                    symbol=e.get("symbol"),
                    kind=e.get("kind"),
                    path=e["path"],
                    start_line=int(e["start_line"]),
                    end_line=int(e["end_line"]),
                    score=float(e["score"]),
                    why=e.get("why") or "",
                    level=e.get("level") or "signature",
                    token_count=int(e.get("token_count") or 0),
                    content_hash=e.get("content_hash") or "",
                    pinned=bool(e.get("pinned")),
                    code=e.get("code") or "",
                )
                for e in payload["entries"]
            ],
            prefetch=[
                PrefetchEntry(
                    region_id=p["region_id"],
                    symbol=p.get("symbol"),
                    path=p["path"],
                    start_line=int(p["start_line"]),
                    end_line=int(p["end_line"]),
                    score=float(p["score"]),
                    p_used_soon=float(p["p_used_soon"]),
                    why=p.get("why") or "",
                    signature=p.get("signature") or "",
                )
                for p in payload.get("prefetch", [])
            ],
            rejected=[
                RejectedEntry(
                    region_id=r["region_id"], symbol=r.get("symbol"), path=r["path"],
                    score=float(r["score"]), reason=r.get("reason") or "",
                )
                for r in payload.get("rejected", [])[:8]
            ],
            evicted=[str(e.get("symbol") or e.get("region_id")) for e in payload.get("evicted", [])],
            stale_blocked=[str(s.get("symbol") or s.get("region_id")) for s in payload.get("stale_blocked", [])],
            explanation_text=explanation,
        )
        bundle.rendered_text = render_bundle(bundle)
        return bundle

    def search(self, req: SearchRequest, requester: str | None = None) -> SearchResponse | ErrorResponse:
        query = _clip(req.query, 500).strip()
        if not query:
            return ErrorResponse(error="empty query", request_type="SearchRequest")
        sid = self.session_for(requester)
        self.runtime.sync()
        res = self.runtime.controller.search(sid, query, budget=_budget(req.budget, min(1200, self.cfg.token_budget)))
        resp = SearchResponse(
            query=query,
            token_count=int(res["token_count"]),
            budget=int(res["budget"]),
            regions=[
                SearchRegion(
                    region_id=r["region_id"], symbol=r.get("symbol"), kind=r.get("kind"), path=r["path"],
                    start_line=int(r["start_line"]), end_line=int(r["end_line"]), score=float(r["score"]),
                    why=r.get("why") or "", signature=r.get("signature") or "",
                )
                for r in res["regions"]
            ],
        )
        resp.rendered_text = render_search(resp)
        return resp

    def explain(self, req: ExplainRequest, requester: str | None = None) -> ExplainResponse | ErrorResponse:
        bid = _clip(req.bundle_id, 120).strip()
        if not bid:
            return ErrorResponse(error="missing bundle_id", request_type="ExplainRequest")
        payload = self.runtime.controller.explain(bid)
        if payload.get("error"):
            return ErrorResponse(error=payload["error"], detail=bid, request_type="ExplainRequest")
        return ExplainResponse(bundle_id=bid, objective=payload.get("objective") or "", text=payload.get("text") or "")

    def trace(self, req: TraceRequest, requester: str | None = None) -> TraceResponse | ErrorResponse:
        from ledger.trace.runner import run_pytest_traced

        args = sanitize_pytest_args(req.pytest_args, self.cfg.repo_root)
        sid = self.session_for(requester, req.task_id)
        self.runtime.sync()
        try:
            res = run_pytest_traced(
                self.cfg, sid, args,
                conn=self.runtime.conn, collector=self.runtime.collector,
                controller=self.runtime.controller, task_id=req.task_id,
            )
        except Exception as ex:  # subprocess / coverage failure
            return ErrorResponse(error="trace failed", detail=_clip(str(ex), 400), request_type="TraceRequest")

        def region_rows(ids: list[str], tier: str) -> list[TraceRegion]:
            out = []
            for rid in ids:
                row = self.runtime.conn.execute(
                    "SELECT region_id, symbol, path, start_line, end_line FROM source_regions WHERE region_id = ?", (rid,)
                ).fetchone()
                if row:
                    out.append(TraceRegion(region_id=row["region_id"], symbol=row["symbol"], path=row["path"],
                                           start_line=int(row["start_line"]), end_line=int(row["end_line"]), tier=tier))
            return out

        regions = (
            region_rows(res.get("frame_region_ids", []), "frame")
            + region_rows(res.get("failing_only_region_ids", []), "failing_only")
            + region_rows(res.get("executed_region_ids", []), "executed")
        )
        resp = TraceResponse(
            passed=bool(res["passed"]),
            returncode=int(res["returncode"]),
            failed_tests=list(res["tests"]["failed"]),
            passed_tests=list(res["tests"]["passed"]),
            frames=[
                TraceFrame(path=f["path"], line=int(f.get("line") or 0), symbol=f.get("symbol"),
                           region_id=f.get("region_id"), nodeid=f.get("nodeid"))
                for f in res.get("frames", [])[:20]
            ],
            regions=regions,
            n_regions=len(res.get("region_ids", [])),
        )
        resp.rendered_text = render_trace(resp, args)
        return resp

    def metrics(self, req: MetricsRequest, requester: str | None = None) -> MetricsResponse | ErrorResponse:
        sid = _clip(req.session_id, 120).strip() or self.session_for(requester)
        m = self.runtime.controller.metrics(sid)
        resp = MetricsResponse(session_id=sid, metrics=m)
        resp.rendered_text = render_metrics(resp)
        return resp

    # ------------------------------------------------------------- chat glue

    def handle_chat_text(self, text: str, requester: str | None = None) -> tuple[str, dict]:
        """Route a natural-language message. Returns (reply_markdown, structured_json)."""
        intent, params = parse_intent(text)
        if intent == Intent.HELP:
            return HELP_TEXT, {"intent": "help"}
        if intent == Intent.EXPLAIN:
            if not params.get("bundle_id"):
                return "Which bundle? Say `explain <bundle_id>` (ids look like `bnd_...`).", {"intent": "explain", "error": "missing bundle_id"}
            out = self.explain(ExplainRequest(bundle_id=params["bundle_id"]), requester)
        elif intent == Intent.METRICS:
            out = self.metrics(MetricsRequest(), requester)
        elif intent == Intent.TRACE:
            out = self.trace(TraceRequest(pytest_args=params.get("pytest_args") or []), requester)
        elif intent == Intent.SEARCH:
            out = self.search(SearchRequest(query=params.get("query") or ""), requester)
        else:
            out = self.context(ContextRequest(objective=params["objective"], seed=params.get("seed")), requester)

        if isinstance(out, ErrorResponse):
            return f"**LEDGER error** ({out.request_type}): {out.error}. {out.detail}".strip(), {"intent": intent, **out.dict()}
        if isinstance(out, ExplainResponse):
            return f"```\n{out.text}\n```", {"intent": intent, "bundle_id": out.bundle_id}
        text_out = getattr(out, "rendered_text", "") or json.dumps(out.dict(), indent=1)[:3000]
        return text_out, {"intent": intent, **compact(out)}


# ------------------------------------------------------------------ rendering


def compact(resp) -> dict:
    """Small machine-readable summary appended to chat replies."""
    if isinstance(resp, ContextBundle):
        return {
            "bundle_id": resp.bundle_id,
            "session_id": resp.session_id,
            "token_count": resp.token_count,
            "budget": resp.budget,
            "entries": [
                {"symbol": e.symbol, "path": e.path, "start_line": e.start_line, "end_line": e.end_line,
                 "score": e.score, "level": e.level}
                for e in resp.entries
            ],
            "prefetch": [{"symbol": p.symbol, "path": p.path, "p_used_soon": p.p_used_soon} for p in resp.prefetch],
        }
    if isinstance(resp, SearchResponse):
        return {"query": resp.query, "token_count": resp.token_count,
                "regions": [{"symbol": r.symbol, "path": r.path, "start_line": r.start_line, "end_line": r.end_line, "score": r.score} for r in resp.regions]}
    if isinstance(resp, TraceResponse):
        return {"passed": resp.passed, "failed_tests": resp.failed_tests,
                "frames": [f.dict() for f in resp.frames[:5]],
                "regions": [{"symbol": r.symbol, "path": r.path, "tier": r.tier} for r in resp.regions[:20]]}
    if isinstance(resp, MetricsResponse):
        keys = ("hits", "misses", "hit_rate", "prefetches", "prefetch_used", "evictions", "invalidations", "stale_blocked",
                "stale_served", "bundles", "ledger_injected_tokens", "repo_tool_calls")
        return {"session_id": resp.session_id, **{k: resp.metrics.get(k) for k in keys}}
    return {}


def render_bundle(b: ContextBundle) -> str:
    lines = [
        f"**LEDGER context bundle** `{b.bundle_id}` — {b.token_count}/{b.budget} tokens, "
        f"{len(b.entries)} region(s), repo `{b.repo}`",
        f"objective: {b.objective}",
        "",
    ]
    for i, e in enumerate(b.entries, 1):
        pin = " 📌" if e.pinned else ""
        lines.append(f"{i}. **{e.symbol or '(module)'}** — `{e.path}:{e.start_line}-{e.end_line}` · score {e.score:.3f} · {e.level}{pin}")
        if e.why:
            lines.append(f"   why: {e.why}")
    if b.prefetch:
        lines.append("")
        lines.append("prefetched (signatures): " + ", ".join(f"`{p.symbol}` p={p.p_used_soon:.2f}" for p in b.prefetch))
    if b.rejected:
        lines.append("rejected: " + ", ".join(f"`{r.symbol}` ({r.reason})" for r in b.rejected[:4]))
    if b.evicted:
        lines.append("evicted: " + ", ".join(f"`{s}`" for s in b.evicted[:6]))
    if b.stale_blocked:
        lines.append("stale (hash mismatch, not served): " + ", ".join(f"`{s}`" for s in b.stale_blocked))
    lines.append("")
    lines.append(f"Say `explain {b.bundle_id}` for the per-feature score breakdown.")
    return "\n".join(lines)


def render_search(s: SearchResponse) -> str:
    lines = [f"**LEDGER search** `{s.query}` — {len(s.regions)} region(s), {s.token_count}/{s.budget} tokens", ""]
    for i, r in enumerate(s.regions, 1):
        lines.append(f"{i}. **{r.symbol or '(module)'}** — `{r.path}:{r.start_line}-{r.end_line}` · score {r.score:.3f}")
        if r.why:
            lines.append(f"   why: {r.why}")
    return "\n".join(lines)


def render_trace(t: TraceResponse, args: list[str]) -> str:
    status = "PASSED" if t.passed else "FAILED"
    lines = [f"**LEDGER trace** `pytest {' '.join(args)}` — {status} (rc={t.returncode}); "
             f"{len(t.failed_tests)} failed, {len(t.passed_tests)} passed; {t.n_regions} region(s) executed", ""]
    if t.failed_tests:
        lines.append("failing tests:")
        lines.extend(f"- `{n}`" for n in t.failed_tests[:10])
    if t.frames:
        lines.append("traceback frames (pinned anchors):")
        lines.extend(f"- `{f.path}:{f.line}` {('→ ' + f.symbol) if f.symbol else ''}" for f in t.frames[:8])
    tiers = {"frame": [], "failing_only": [], "executed": []}
    for r in t.regions:
        tiers.setdefault(r.tier, []).append(r)
    if tiers["failing_only"]:
        lines.append("executed only by failing tests:")
        lines.extend(f"- **{r.symbol}** `{r.path}:{r.start_line}-{r.end_line}`" for r in tiers["failing_only"][:10])
    if tiers["executed"]:
        lines.append(f"also executed ({len(tiers['executed'])}): " + ", ".join(f"`{r.symbol}`" for r in tiers["executed"][:12]))
    lines.append("")
    lines.append("Execution evidence is now joined into your working set; ask for context again to see pinned regions rank first.")
    return "\n".join(lines)


def render_metrics(m: MetricsResponse) -> str:
    d = m.metrics
    return "\n".join([
        f"**LEDGER metrics** session `{m.session_id}`",
        f"- bundles served: {d.get('bundles')} · injected tokens: {d.get('ledger_injected_tokens')} · repo tool calls: {d.get('repo_tool_calls')}",
        f"- hits/misses: {d.get('hits')}/{d.get('misses')} (hit rate {d.get('hit_rate')})",
        f"- prefetches: {d.get('prefetches')} (used {d.get('prefetch_used')}, precision {d.get('prefetch_precision')})",
        f"- evictions: {d.get('evictions')} · invalidations: {d.get('invalidations')} · stale blocked: {d.get('stale_blocked')} · stale served: {d.get('stale_served')}",
        f"- admitted: {d.get('admitted')} · pollution rate: {d.get('pollution_rate')}",
    ])
