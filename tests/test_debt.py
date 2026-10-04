"""Technical-debt kinds, ordering, and the demo_repo retry-cycle SCC."""

from fastapi.testclient import TestClient

from traceweaver.api.app import DASHBOARD_HTML, create_app
from traceweaver.audit import build_audit
from traceweaver.graphalg.debt import (
    CHOKE_WEIGHT,
    COMPLEX_WEIGHT,
    LINES_THRESHOLD,
    TANGLED_WEIGHT,
    analyze_debt,
    debt_from_conn,
)


def _quantize(value: float) -> int | float:
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _region(symbol: str, *, lines: int = 4, body: str = "", path: str = "synth.py") -> dict:
    return {
        "region_id": symbol,
        "path": path,
        "symbol": symbol,
        "kind": "function",
        "start_line": 1,
        "end_line": lines,
        "body": body,
    }


def _synthetic():
    graph = {
        "cycle_a": ["cycle_b"],
        "cycle_b": ["cycle_c", "choke"],
        "cycle_c": ["cycle_a"],
        "choke": ["leaf"],
        "leaf": [],
        "fat": ["cycle_a", "cycle_b", "cycle_c", "choke"],
        "orphan": [],
    }
    fat_body = "if a:\n    pass\nif b:\n    pass\nif c:\n    pass\n"
    regions = {
        "cycle_a": _region("cycle_a"),
        "cycle_b": _region("cycle_b"),
        "cycle_c": _region("cycle_c"),
        "choke": _region("choke"),
        "leaf": _region("leaf"),
        "fat": _region("fat", lines=48, body=fat_body),
        "orphan": _region("orphan"),
    }
    return graph, regions


def test_synthetic_kinds_order_and_deterministic_costs():
    graph, regions = _synthetic()
    report = analyze_debt(graph, regions)
    again = analyze_debt(graph, regions)
    assert again == report
    assert report["simulated"] is True
    assert report["unit"] == "debt-tokens"
    assert report["total_debt"] > 0
    assert report["dollar_equivalent"] == report["total_debt"]
    assert report["total_debt"] == _quantize(sum(f["cost"] for f in report["findings"]))

    kinds = [f["kind"] for f in report["findings"]]
    assert kinds == ["complex", "tangled", "choke", "orphan"]
    costs = [f["cost"] for f in report["findings"]]
    assert costs == sorted(costs, reverse=True)
    assert all(c > 0 for c in costs)

    fat, tangled, choke, orphan = report["findings"]
    assert fat["region_ids"] == ["fat"]
    fb = fat["breakdown"]
    assert fat["cost"] == _quantize(
        (fb["lines"] / fb["lines_threshold"]) * fb["fan_out"] * (1 + fb["branch_count"]) * fb["weight"]
    )
    assert fb["lines"] == 48
    assert fb["fan_out"] == 4
    assert fb["branch_count"] == 3
    assert fb["lines_threshold"] == LINES_THRESHOLD
    assert fb["weight"] == COMPLEX_WEIGHT
    assert fat["graph_node_ids"] == ["fat"]

    assert set(tangled["region_ids"]) == {"cycle_a", "cycle_b", "cycle_c"}
    tb = tangled["breakdown"]
    assert tangled["cost"] == tb["scc_size"] * tb["internal_edges"] * tb["weight"]
    assert tb["scc_size"] == 3
    assert tb["internal_edges"] == 3
    assert tb["weight"] == TANGLED_WEIGHT
    assert tangled["graph_node_ids"] == tangled["region_ids"]

    assert choke["region_ids"] == ["choke"]
    cb = choke["breakdown"]
    assert choke["cost"] == cb["cut_crossing_edges"] * cb["dependent_region_count"] * cb["weight"]
    assert cb["dependent_region_count"] >= 1
    assert cb["cut_crossing_edges"] >= 1
    assert cb["weight"] == CHOKE_WEIGHT
    assert "leaf" not in choke["region_ids"]

    assert orphan["region_ids"] == ["orphan"]
    assert orphan["cost"] > 0
    assert report["formula"]["tangled_cost"].startswith("scc_size * internal_edges")
    assert report["formula"]["choke_cost"].startswith("cut_crossing_edges * dependent_region_count")
    assert "fan_out" in report["formula"]["complex_cost"]
    assert report["highlight"]["choke"] == "choke"
    assert report["highlight"]["cycle_a"] == "tangled"
    assert report["highlight"]["fat"] == "complex"


def test_demo_repo_debt_prices_retry_cycle(demo_rt):
    report = debt_from_conn(demo_rt.conn)
    again = debt_from_conn(demo_rt.conn)
    assert again["total_debt"] == report["total_debt"]
    assert [f["cost"] for f in again["findings"]] == [f["cost"] for f in report["findings"]]
    assert isinstance(report["total_debt"], (int, float))
    assert report["total_debt"] > 0
    assert report["simulated"] is True

    tangled = [f for f in report["findings"] if f["kind"] == "tangled"]
    assert tangled
    retry = next(f for f in tangled if any("schedule_retry" in region for region in f["regions"]))
    breakdown = retry["breakdown"]
    assert breakdown["scc_size"] > 1
    assert retry["cost"] == breakdown["scc_size"] * breakdown["internal_edges"] * breakdown["weight"]
    assert retry["cost"] > 0
    blob = " ".join(retry["regions"])
    assert "SubscriptionService.renew" in blob
    assert "RenewalWebhook.handle" in blob

    example = "\n".join(report["worked_example"]["arithmetic"])
    assert "for_renewal" in example
    assert "schedule_retry" in example
    assert "*" in example
    assert report["worked_example"]["min_cut_weight"] is not None
    assert report["worked_example"]["graph_node_ids"]

    audit = build_audit(demo_rt.cfg)
    assert audit["debt"]["total_debt"] == report["total_debt"]
    assert any("schedule_retry" in " ".join(f["regions"]) for f in audit["debt"]["findings"] if f["kind"] == "tangled")

    app = create_app(demo_rt)
    client = TestClient(app)
    body = client.get("/api/debt").json()
    assert body["total_debt"] == report["total_debt"]
    assert body["formula"]["tangled_cost"]
    html = DASHBOARD_HTML.read_text(encoding="utf-8")
    assert "Technical debt" in html
    assert "/api/debt" in html
    assert 'id="debt"' in html
