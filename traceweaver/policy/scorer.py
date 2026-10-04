"""Explainable first-pass ranking (spec §8.2).

S(r,t) = αL + βX + γG + δH + εR + ζD − λC − μF

Every feature is normalised to [0,1] and every weighted contribution is
returned so ``traceweaver_explain`` can show the breakdown deterministically.
"""

from __future__ import annotations

import math
import sqlite3

from traceweaver.config import TraceWeaverConfig
from traceweaver.index.lexical import tokenize


def _clip(x: float) -> float:
    return max(0.0, min(1.0, x))


def score_region(
    conn: sqlite3.Connection,
    cfg: TraceWeaverConfig,
    session_id: str,
    region: dict,
    objective: str,
    turn: int,
    ws: dict | None = None,
) -> dict:
    rid = region["region_id"]
    if ws is None:
        row = conn.execute(
            "SELECT * FROM working_set WHERE session_id = ? AND region_id = ?", (session_id, rid)
        ).fetchone()
        ws = dict(row) if row else {}
    sources = region.get("candidate_sources") or []

    # L: lexical / intent similarity (only lexical evidence counts here)
    sw = region.get("source_weights") or {}
    lex_weight = sw.get("lexical", 0) + sw.get("seed_lexical", 0) + sw.get("symbol", 0)
    L = _clip(lex_weight / 2.5)
    obj_tokens = set(tokenize(objective))
    hay_tokens = set(tokenize(f"{region.get('symbol','')} {region.get('path','')} {region.get('signature','')}"))
    if obj_tokens:
        overlap = len(obj_tokens & hay_tokens) / len(obj_tokens)
        L = _clip(L + 0.5 * overlap)
    if "symbol" in sources:
        L = max(L, 0.75)

    # X: dynamic execution relevance
    X = _clip(float(ws.get("execution_score", 0) or 0))

    # G: static structural proximity
    G = 0.1
    if "callee" in sources or "caller" in sources:
        G = 0.8
    if "callee" in sources and "caller" in sources:
        G = 1.0
    if "prefetch_edge" in sources:
        # Resolved call-graph neighbour of an admitted line: stronger than a
        # generic union candidate, weaker than an explicit caller/callee source.
        G = max(G, 0.85)

    # H: historical co-access
    H = _clip(float(ws.get("co_access_score", 0) or 0))
    if "co_access" in sources:
        H = max(H, 0.6)
    if "co_access_history" in sources:
        H = max(H, 0.4)

    # R: recency / frequency
    last = int(ws.get("last_access_turn", 0) or 0)
    recency = math.exp(-0.35 * max(0, turn - last)) if last else 0.0
    freq = _clip(float(ws.get("access_frequency", 0) or 0) / 6.0)
    R = _clip(0.6 * recency + 0.4 * freq)

    # D: diagnostic / diff relevance
    D = _clip(float(ws.get("edit_likelihood", 0) or 0))
    if "diagnostic" in sources:
        D = max(D, 0.9)
    if "diff" in sources:
        D = max(D, 0.6)

    # C: token cost relative to budget
    C = _clip(float(region.get("token_count") or 0) / max(cfg.token_budget, 1))

    # F: staleness
    F = _clip(float(ws.get("staleness", 0) or 0))
    if int(ws.get("stale", 0) or 0):
        F = 1.0
    if region.get("kind") == "module":
        # whole modules are coarse; prefer their functions
        C = min(1.0, C + 0.3)

    w = cfg.weights
    parts = {
        "lexical": w["lexical"] * L,
        "execution": w["execution"] * X,
        "structural": w["structural"] * G,
        "co_access": w["co_access"] * H,
        "recency": w["recency"] * R,
        "diagnostic": w["diagnostic"] * D,
        "token_cost": w["token_cost"] * C,
        "staleness": w["staleness"] * F,
    }
    # Extra signal only when a vector index already exists. An empty index
    # leaves the weights above unchanged. Published 12-task numbers were
    # measured before this cosine term.
    sem, sem_note = _semantic(conn, cfg, region, objective)
    if sem is not None:
        parts["semantic"] = w.get("semantic", 0.16) * sem
    score = sum(parts.values())
    return {
        "score": round(score, 4),
        "parts": {k: round(v, 4) for k, v in parts.items()},
        "features": {"L": round(L, 3), "X": round(X, 3), "G": round(G, 3), "H": round(H, 3), "R": round(R, 3), "D": round(D, 3), "C": round(C, 3), "F": round(F, 3), "Sem": round(sem, 3) if sem is not None else 0.0},
        "why": _why(sources, X, D, F, L, H, R, float(ws.get("agent_access_score") or 0), float(ws.get("edit_likelihood") or 0), sem_note),
        "p_used_soon": round(1 / (1 + math.exp(-6.0 * (score - 0.30))), 4),
    }


def _semantic(conn, cfg, region: dict, objective: str) -> tuple[float | None, str]:
    try:
        from traceweaver.index.embeddings import open_index

        idx = open_index(cfg, conn)
        if idx.count() == 0:
            return None, ""
        cos = idx.cosine_for(region.get("region_id") or "", objective or "")
        if cos is None:
            return None, ""
        cos = _clip(cos)
        return cos, f"semantic neighbor (cosine {cos:.2f}, {idx.backend})"
    except Exception:
        return None, ""


def _why(sources: list[str], X: float, D: float, F: float, L: float, H: float, R: float, agent_access: float, edit_likelihood: float, semantic_note: str = "") -> str:
    bits = []
    if X >= 0.9:
        bits.append("executed only by the failing test")
    elif X > 0.4:
        bits.append("executed by targeted test")
    if "diagnostic" in sources:
        bits.append("in failing-test traceback")
    elif edit_likelihood >= 0.85:
        bits.append("edited this session")
    if "symbol" in sources:
        bits.append("exact symbol match")
    elif L > 0.45:
        bits.append("intent/lexical match")
    if "callee" in sources:
        bits.append("call successor of active region")
    if "caller" in sources:
        bits.append("caller of active region")
    if "co_access" in sources or H > 0.5:
        bits.append("co-accessed this session")
    if "co_access_history" in sources:
        bits.append("co-accessed in prior tasks")
    if "diff" in sources:
        bits.append("changed in working tree")
    if R > 0.5 and agent_access > 0:
        bits.append("read this session")
    if "prefetch_edge" in sources:
        bits.append("prefetched call edge")
    if F > 0.5:
        bits.append("STALE — content hash mismatch")
    if semantic_note:
        bits.append(semantic_note)
    if not bits:
        bits.append("weak union candidate")
    return "; ".join(bits)
