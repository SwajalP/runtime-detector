"""Tarjan, reverse BFS, Stoer–Wagner, and the demo_repo for_renewal neighborhood."""

from ledger.graphalg.algorithms import reverse_bfs, stoer_wagner, tarjan_scc
from ledger.graphalg.apply import symbol_neighborhood


def _sets(components: list[list[str]]) -> set[frozenset[str]]:
    return {frozenset(comp) for comp in components}


def test_tarjan_known_sccs():
    graph = {
        "a": ["b"],
        "b": ["c"],
        "c": ["a"],
        "d": ["c"],
        "e": ["f"],
        "f": ["e"],
        "g": [],
    }
    found = _sets(tarjan_scc(graph))
    assert frozenset({"a", "b", "c"}) in found
    assert frozenset({"e", "f"}) in found
    assert frozenset({"d"}) in found
    assert frozenset({"g"}) in found
    assert len(found) == 4


def test_reverse_bfs_order():
    graph = {"A": ["B"], "B": ["C"], "D": ["B"], "C": []}
    assert reverse_bfs(graph, "C") == ["C", "B", "A", "D"]


def test_stoer_wagner_barbell_min_cut():
    def clique(names: list[str]) -> dict[str, dict[str, float]]:
        adj: dict[str, dict[str, float]] = {n: {} for n in names}
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                adj[a][b] = 1.0
                adj[b][a] = 1.0
        return adj

    left_names = ["a1", "a2", "a3"]
    right_names = ["b1", "b2", "b3"]
    adj = clique(left_names)
    for node, nbrs in clique(right_names).items():
        adj[node] = nbrs
    adj["a1"]["b1"] = 1.0
    adj["b1"]["a1"] = 1.0

    cut = stoer_wagner(adj)
    assert cut["weight"] == 1.0
    sides = {frozenset(cut["left"]), frozenset(cut["right"])}
    assert sides == {frozenset(left_names), frozenset(right_names)}


def test_stoer_wagner_single_edge():
    cut = stoer_wagner({"a": {"b": 4.0}, "b": {"a": 4.0}})
    assert cut["weight"] == 4.0
    assert {frozenset(cut["left"]), frozenset(cut["right"])} == {frozenset({"a"}), frozenset({"b"})}


def test_for_renewal_neighborhood_is_non_empty(demo_rt):
    info = symbol_neighborhood(
        demo_rt.conn,
        "for_renewal",
        path_substr="shop/billing/discount_policy.py",
    )
    assert info["seed"] == "shop/billing/discount_policy.py::for_renewal"
    assert info["neighborhood"]
    assert info["labels"]
    assert any("subscription_service.py" in label or "loyalty_discount" in label for label in info["labels"])


def test_renewal_bundle_keeps_for_renewal_and_graph_tags(demo_rt):
    sid = demo_rt.new_session(agent="test", condition="ledger", task_id="renewal-discount")
    bundle = demo_rt.controller.build_bundle(
        sid,
        objective="renewal invoices ignore loyalty discounts",
        seed="for_renewal",
    )
    hits = [
        e
        for e in bundle["entries"]
        if e.get("symbol") == "for_renewal" and e.get("path") == "shop/billing/discount_policy.py"
    ]
    assert hits, [f"{e.get('path')}::{e.get('symbol')}" for e in bundle["entries"]]
    tags = set(hits[0].get("graph_tags") or [])
    assert {"reverse_bfs", "min_cut", "tarjan"} <= tags
    text = demo_rt.controller.explain(bundle["bundle_id"])["text"]
    for tag in ("reverse_bfs", "min_cut", "tarjan"):
        assert tag in text
