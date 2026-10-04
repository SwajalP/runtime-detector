"""Message models for the ``TraceWeaverContextProtocol``.

These are uAgents ``Model``s (pydantic **v1** under the hood in
``uagents_core``), so annotations stay v1-compatible. Every inbound string is
treated strictly as data: it is passed to the TraceWeaver controller as a query /
objective, never executed or interpolated into a shell.

Interaction table (request -> replies):

    ContextRequest  -> ContextBundle  | ErrorResponse
    SearchRequest   -> SearchResponse | ErrorResponse
    ExplainRequest  -> ExplainResponse| ErrorResponse
    TraceRequest    -> TraceResponse  | ErrorResponse
    MetricsRequest  -> MetricsResponse| ErrorResponse
"""

from __future__ import annotations

from typing import Optional

from uagents import Model


# --------------------------------------------------------------------- shared


class RegionEntry(Model):
    """One admitted source region in a served bundle."""

    region_id: str
    symbol: Optional[str] = None
    kind: Optional[str] = None
    path: str
    start_line: int
    end_line: int
    score: float
    why: str = ""
    level: str = "signature"  # signature | summary | exact
    token_count: int = 0
    content_hash: str = ""
    pinned: bool = False
    code: str = ""


class PrefetchEntry(Model):
    region_id: str
    symbol: Optional[str] = None
    path: str
    start_line: int
    end_line: int
    score: float
    p_used_soon: float
    why: str = ""
    signature: str = ""


class RejectedEntry(Model):
    region_id: str
    symbol: Optional[str] = None
    path: str
    score: float
    reason: str = ""


class ErrorResponse(Model):
    error: str
    detail: str = ""
    request_type: str = ""


# -------------------------------------------------------------------- context


class ContextRequest(Model):
    """Ask TraceWeaver for a token-budgeted context bundle for an objective."""

    objective: str
    seed: Optional[str] = None  # optional symbol / path hint
    budget: Optional[int] = None  # tokens; defaults to the repo's configured budget
    repo: Optional[str] = None  # informational; must match the served repo
    task_id: Optional[str] = None


class ContextBundle(Model):
    bundle_id: str
    session_id: str
    objective: str
    repo: str
    token_count: int
    budget: int
    entries: list[RegionEntry]
    prefetch: list[PrefetchEntry] = []
    rejected: list[RejectedEntry] = []
    evicted: list[str] = []
    stale_blocked: list[str] = []
    explanation_text: str = ""
    rendered_text: str = ""


# --------------------------------------------------------------------- search


class SearchRequest(Model):
    query: str
    budget: Optional[int] = None


class SearchRegion(Model):
    region_id: str
    symbol: Optional[str] = None
    kind: Optional[str] = None
    path: str
    start_line: int
    end_line: int
    score: float
    why: str = ""
    signature: str = ""


class SearchResponse(Model):
    query: str
    token_count: int
    budget: int
    regions: list[SearchRegion]
    rendered_text: str = ""


# -------------------------------------------------------------------- explain


class ExplainRequest(Model):
    bundle_id: str


class ExplainResponse(Model):
    bundle_id: str
    objective: str = ""
    text: str


# ---------------------------------------------------------------------- trace


class TraceRequest(Model):
    """Run pytest under coverage and join the program trace with source regions."""

    pytest_args: list[str] = []  # e.g. ["-q", "tests/test_renewal_discount.py"]
    task_id: Optional[str] = None


class TraceFrame(Model):
    path: str
    line: int
    symbol: Optional[str] = None
    region_id: Optional[str] = None
    nodeid: Optional[str] = None


class TraceRegion(Model):
    region_id: str
    symbol: Optional[str] = None
    path: str
    start_line: int
    end_line: int
    tier: str  # frame | failing_only | executed


class TraceResponse(Model):
    passed: bool
    returncode: int
    failed_tests: list[str]
    passed_tests: list[str]
    frames: list[TraceFrame]
    regions: list[TraceRegion]
    n_regions: int
    rendered_text: str = ""


# -------------------------------------------------------------------- metrics


class MetricsRequest(Model):
    session_id: Optional[str] = None


class MetricsResponse(Model):
    session_id: str
    metrics: dict
    rendered_text: str = ""


class ChatText(Model):
    """Plain-text chat turn (keyword-routed by :func:`service.parse_intent`)."""

    text: str


REQUEST_MODELS = (ContextRequest, SearchRequest, ExplainRequest, TraceRequest, MetricsRequest, ChatText)
RESPONSE_MODELS = (ContextBundle, SearchResponse, ExplainResponse, TraceResponse, MetricsResponse, ErrorResponse, ChatText)
