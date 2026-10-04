"""Admission control (spec §8.3).

EV(r) = P(used soon | state) · M(r) − A(r) − P_pollution(r)

M: miss cost avoided (a future tool call plus the tokens of a whole-file read),
A: admission cost (tokens added now plus lookup overhead),
P_pollution: expected harm from displacing better context, scaled by how much
of the budget the region would consume.
"""

from __future__ import annotations

from traceweaver.config import TraceWeaverConfig

TOOL_CALL_COST_TOKENS = 600  # approximate overhead of one more grep/read round-trip
LOOKUP_COST_TOKENS = 30


def expected_value(score_info: dict, region: dict, cfg: TraceWeaverConfig) -> dict:
    p = float(score_info["p_used_soon"])
    tokens = float(region.get("token_count") or 0)
    # If the agent later needs this region it will typically read the whole file.
    file_tokens = float(region.get("file_token_count") or max(tokens * 3, tokens + 400))
    miss_cost = TOOL_CALL_COST_TOKENS + min(file_tokens, 6000)
    admit_cost = tokens + LOOKUP_COST_TOKENS
    pollution = 400 * (1 - p) * (tokens / max(cfg.token_budget, 1))
    ev = p * miss_cost - admit_cost - pollution
    return {
        "ev": round(ev, 1),
        "p_used_soon": p,
        "miss_cost": round(miss_cost, 1),
        "admit_cost": round(admit_cost, 1),
        "pollution": round(pollution, 1),
    }


def should_admit(score_info: dict, region: dict, cfg: TraceWeaverConfig) -> tuple[bool, str]:
    feats = score_info["features"]
    if feats["F"] >= 1.0:
        return False, "stale"
    if score_info["score"] < cfg.admission_threshold and feats["X"] < 0.5 and feats["D"] < 0.8:
        return False, f"score {score_info['score']:.2f} < threshold {cfg.admission_threshold:.2f}"
    ev = expected_value(score_info, region, cfg)
    if ev["ev"] <= 0:
        return False, f"EV {ev['ev']:.0f} ≤ 0 (p={ev['p_used_soon']:.2f}, admit={ev['admit_cost']:.0f})"
    return True, f"EV +{ev['ev']:.0f} (p={ev['p_used_soon']:.2f})"
