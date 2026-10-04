"""Technical-debt cost on the indexed region call graph.

Debt-tokens are a deterministic structural unit computed from the call graph
already in memory. One debt-token is shown as $1 of estimated attention.
This is a simulated local analysis, not a live model score.

Formulas (weights are the constants below):

    tangled_cost = scc_size * internal_edges * TANGLED_WEIGHT
    choke_cost   = cut_crossing_edges * dependent_region_count * CHOKE_WEIGHT
    complex_cost = (lines / LINES_THRESHOLD) * fan_out * (1 + branch_count) * COMPLEX_WEIGHT
    hub_cost     = (in_degree + out_degree) * HUB_WEIGHT
    orphan_cost  = ORPHAN_WEIGHT
    total        = sum of finding costs

Tarjan SCC, reverse BFS, and Stoer–Wagner are reused from ``graphalg``.
Articulation points are a separate vertex-cut pass on the undirected graph.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections import deque

from traceweaver.graphalg.algorithms import reverse_bfs, stoer_wagner, tarjan_scc
from traceweaver.graphalg.clones import clones_from_conn
from traceweaver.graphalg.apply import (
    FAILING_PATH,
    FAILING_SYMBOL,
    call_graph,
    resolve_region,
    symbol_neighborhood,
    undirected_component,
)

TANGLED_WEIGHT = 8
CHOKE_WEIGHT = 12
COMPLEX_WEIGHT = 5
HUB_WEIGHT = 2
ORPHAN_WEIGHT = 1
LINES_THRESHOLD = 12
COMPLEX_FAN_OUT = 4
COMPLEX_BRANCHES = 4
HUB_MIN_DEGREE = 5
ORPHAN_CAP = 8
BETWEENNESS_NODE_LIMIT = 400

_BRANCH = re.compile(r"(?m)^\s*(?:elif|else|if|for|while|except|case)\b")
_KIND_ORDER = {"choke": 0, "tangled": 1, "complex": 2, "hub": 3, "orphan": 4}
_HIGHLIGHT_KINDS = ("choke", "tangled", "complex", "hub")
_RETRY_MARKERS = ("schedule_retry", "SubscriptionService.renew", "RenewalWebhook.handle")

DISCLAIMER = (
    "Simulated local analysis of the indexed call graph. "
    "Debt-tokens are a deterministic structural cost from the formulas in this payload. "
    "1 debt-token = $1 of estimated attention, the same number as the total."
)


def formula_spec() -> dict:
    return {
        "unit": "debt-tokens",
        "dollar_rate": "1 debt-token = $1 estimated attention",
        "tangled_cost": f"scc_size * internal_edges * {TANGLED_WEIGHT}",
        "choke_cost": f"cut_crossing_edges * dependent_region_count * {CHOKE_WEIGHT}",
        "complex_cost": (
            f"(lines / {LINES_THRESHOLD}) * fan_out * (1 + branch_count) * {COMPLEX_WEIGHT}"
        ),
        "hub_cost": f"(in_degree + out_degree) * {HUB_WEIGHT}",
        "orphan_cost": str(ORPHAN_WEIGHT),
        "total": "sum of finding costs",
        "tangled_rule": (
            "Tarjan SCC with size > 1. internal_edges counts directed calls "
            "whose endpoints are distinct and both inside the SCC."
        ),
        "choke_rule": (
            "Articulation point of the undirected call graph that has at least one caller "
            "and one callee, so it sits between them. "
            "cut_crossing_edges is its undirected degree (edges severed by removing it). "
            "dependent_region_count is how many regions in its component fall outside "
            "the largest piece that remains. Sinks and roots are not choke points."
        ),
        "complex_bar": (
            f"Reported when fan_out >= 1 and (lines >= {LINES_THRESHOLD} "
            f"or fan_out >= {COMPLEX_FAN_OUT} or branch_count >= {COMPLEX_BRANCHES}). "
            "lines are the region's indexed span. fan_out is distinct callees. "
            "branch_count is leading if/elif/else/for/while/except/case lines in the region text."
        ),
        "hub_rule": (
            f"in_degree + out_degree >= {HUB_MIN_DEGREE}, and the region is not already a choke. "
            "A choke is the stronger claim and is not billed again as a hub."
        ),
        "orphan_rule": (
            f"in_degree and out_degree are both 0. At most {ORPHAN_CAP} are priced, "
            "sorted by name. Further isolates are counted in omitted_orphans and not billed."
        ),
    }


def _num(value: float) -> int | float:
    rounded = round(float(value), 2)
    if rounded == int(rounded):
        return int(rounded)
    return rounded


def _lines(region: dict) -> int:
    start = int(region.get("start_line") or 1)
    end = int(region.get("end_line") or start)
    return max(1, end - start + 1)


def _branch_count(body: str) -> int:
    return len(_BRANCH.findall(body or ""))


def _label(region: dict, rid: str) -> str:
    path = region.get("path") or ""
    symbol = region.get("symbol") or ""
    if path and symbol:
        return f"{path}::{symbol}"
    return symbol or path or rid


def _short_name(labels: list[str]) -> str:
    symbols = [(label.split("::")[-1] if label else label) for label in labels]
    shown = ", ".join(symbols[:3])
    extra = len(symbols) - 3
    if extra > 0:
        shown += f" +{extra}"
    return shown or "(region)"


def _finding_id(kind: str, region_ids: list[str]) -> str:
    raw = kind + "|" + ",".join(region_ids)
    return kind + ":" + hashlib.sha256(raw.encode()).hexdigest()[:12]


def _undirected(graph: dict[str, list[str]]) -> dict[str, list[str]]:
    adj: dict[str, set[str]] = {n: set() for n in graph}
    for u, vs in graph.items():
        adj.setdefault(u, set())
        for v in vs:
            if u == v:
                continue
            adj.setdefault(v, set())
            adj[u].add(v)
            adj[v].add(u)
    return {n: sorted(vs) for n, vs in adj.items()}


def _articulation_points(adj: dict[str, list[str]]) -> set[str]:
    """Vertices whose removal increases the number of connected components."""
    disc: dict[str, int] = {}
    low: dict[str, int] = {}
    parent: dict[str, str | None] = {}
    ap: set[str] = set()
    timer = 0

    def dfs(u: str) -> None:
        nonlocal timer
        disc[u] = low[u] = timer
        timer += 1
        children = 0
        for v in adj[u]:
            if v not in disc:
                parent[v] = u
                children += 1
                dfs(v)
                low[u] = min(low[u], low[v])
                if parent.get(u) is None and children > 1:
                    ap.add(u)
                if parent.get(u) is not None and low[v] >= disc[u]:
                    ap.add(u)
            elif v != parent.get(u):
                low[u] = min(low[u], disc[v])

    for n in sorted(adj):
        if n not in disc:
            parent[n] = None
            dfs(n)
    return ap


def _component(adj: dict[str, list[str]], start: str) -> set[str]:
    seen: set[str] = set()
    stack = [start]
    while stack:
        u = stack.pop()
        if u in seen:
            continue
        seen.add(u)
        stack.extend(adj.get(u, []))
    return seen


def _split_sizes(adj: dict[str, list[str]], node: str) -> list[int]:
    """Sizes of the pieces of ``node``'s component after ``node`` is removed."""
    universe = _component(adj, node)
    universe.discard(node)
    seen: set[str] = set()
    sizes: list[int] = []
    for start in sorted(universe):
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        n = 0
        while stack:
            u = stack.pop()
            n += 1
            for v in adj[u]:
                if v != node and v not in seen and v in universe:
                    seen.add(v)
                    stack.append(v)
        sizes.append(n)
    return sizes


def _betweenness(adj: dict[str, list[str]]) -> dict[str, float] | None:
    nodes = sorted(adj)
    if len(nodes) > BETWEENNESS_NODE_LIMIT:
        return None
    score = {n: 0.0 for n in nodes}
    for s in nodes:
        stack: list[str] = []
        pred: dict[str, list[str]] = {n: [] for n in nodes}
        sigma = {n: 0.0 for n in nodes}
        sigma[s] = 1.0
        dist = {n: -1 for n in nodes}
        dist[s] = 0
        q: deque[str] = deque([s])
        while q:
            v = q.popleft()
            stack.append(v)
            for w in adj[v]:
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    q.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = {n: 0.0 for n in nodes}
        while stack:
            w = stack.pop()
            for v in sorted(pred[w]):
                if sigma[w]:
                    delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
            if w != s:
                score[w] += delta[w]
    if len(nodes) > 1:
        score = {n: value / 2.0 for n, value in score.items()}
    return score


def _degrees(graph: dict[str, list[str]]) -> tuple[dict[str, int], dict[str, int]]:
    incoming = {n: 0 for n in graph}
    outgoing = {n: 0 for n in graph}
    for u, vs in graph.items():
        outgoing[u] = len([v for v in vs if v != u])
        for v in vs:
            if v == u:
                continue
            incoming[v] = incoming.get(v, 0) + 1
    return incoming, outgoing


def _region_for(regions: dict[str, dict], rid: str) -> dict:
    found = regions.get(rid)
    if found:
        return found
    return {
        "region_id": rid,
        "path": "",
        "symbol": rid,
        "kind": "function",
        "start_line": 1,
        "end_line": 1,
        "body": "",
    }


def _highlight(findings: list[dict]) -> dict[str, str]:
    marks: dict[str, str] = {}
    for kind in _HIGHLIGHT_KINDS:
        for finding in findings:
            if finding["kind"] != kind:
                continue
            for rid in finding["region_ids"]:
                marks.setdefault(rid, kind)
    return marks


def _shell(findings: list[dict], *, regions_n: int, edges_n: int, omitted_orphans: int) -> dict:
    findings = sorted(findings, key=lambda f: (-f["cost"], _KIND_ORDER.get(f["kind"], 9), f["name"], f["id"]))
    total = _num(sum(f["cost"] for f in findings)) if findings else 0
    counts = {kind: 0 for kind in ("choke", "tangled", "complex", "hub", "orphan")}
    for finding in findings:
        counts[finding["kind"]] = counts.get(finding["kind"], 0) + 1
    marks = _highlight(findings)
    return {
        "kind": "technical-debt",
        "simulated": True,
        "unit": "debt-tokens",
        "disclaimer": DISCLAIMER,
        "formula": formula_spec(),
        "total_debt": total,
        "dollar_equivalent": total,
        "regions": regions_n,
        "directed_edges": edges_n,
        "counts": counts,
        "omitted_orphans": omitted_orphans,
        "findings": findings,
        "highlight": marks,
        "highlight_ids": sorted(marks),
        "worked_example": {
            "focus": "for_renewal / retry-cycle",
            "arithmetic": [],
            "graph_node_ids": [],
            "min_cut_weight": None,
        },
    }


def analyze_debt(graph: dict[str, list[str]], regions: dict[str, dict] | None = None) -> dict:
    """Classify ``graph`` and price each finding. Pure: same inputs, same output."""
    regions = regions or {}
    graph = {n: list(vs) for n, vs in graph.items()}
    for vs in graph.values():
        for v in vs:
            graph.setdefault(v, [])
    edges_n = sum(len(vs) for vs in graph.values())
    if not graph:
        return _shell([], regions_n=0, edges_n=0, omitted_orphans=0)

    adj = _undirected(graph)
    incoming, outgoing = _degrees(graph)
    points = _articulation_points(adj)
    between = _betweenness(adj)
    findings: list[dict] = []

    for comp in tarjan_scc(graph):
        if len(comp) < 2:
            continue
        members = sorted(comp)
        member_set = set(members)
        internal = 0
        for u in members:
            for v in graph.get(u, []):
                if v in member_set and v != u:
                    internal += 1
        if internal < 1:
            continue
        cost = _num(len(members) * internal * TANGLED_WEIGHT)
        labels = [_label(_region_for(regions, rid), rid) for rid in members]
        findings.append(
            {
                "id": _finding_id("tangled", members),
                "kind": "tangled",
                "name": _short_name(labels),
                "region_ids": members,
                "regions": labels,
                "graph_node_ids": list(members),
                "cost": cost,
                "why": (
                    f"Tarjan SCC of {len(members)} regions with {internal} internal call edges. "
                    "A change inside the cycle can reach every member before it settles."
                ),
                "breakdown": {
                    "scc_size": len(members),
                    "internal_edges": internal,
                    "weight": TANGLED_WEIGHT,
                    "arithmetic": f"{len(members)} * {internal} * {TANGLED_WEIGHT} = {cost}",
                },
            }
        )

    for node in sorted(points):
        sizes = _split_sizes(adj, node)
        if not sizes:
            continue
        largest = max(sizes)
        dependent = sum(sizes) - largest
        crossing = len(adj.get(node, []))
        if dependent < 1 or crossing < 1:
            continue
        # A sink or a root does not lie on a caller→callee path.
        if incoming.get(node, 0) < 1 or outgoing.get(node, 0) < 1:
            continue
        cost = _num(crossing * dependent * CHOKE_WEIGHT)
        region = _region_for(regions, node)
        label = _label(region, node)
        btw = None if between is None else round(float(between.get(node, 0.0)), 1)
        btw_note = f" Betweenness {btw:.1f} on the undirected graph." if btw is not None else ""
        dep_word = "region" if dependent == 1 else "regions"
        piece_word = "region" if largest == 1 else "regions"
        findings.append(
            {
                "id": _finding_id("choke", [node]),
                "kind": "choke",
                "name": _short_name([label]),
                "region_ids": [node],
                "regions": [label],
                "graph_node_ids": [node],
                "cost": cost,
                "why": (
                    f"Removing {label} severs {crossing} call edges and leaves {dependent} "
                    f"{dep_word} outside the largest remaining piece ({largest} {piece_word}). "
                    "A bug here stalls callers from callees."
                    + btw_note
                ),
                "breakdown": {
                    "cut_crossing_edges": crossing,
                    "dependent_region_count": dependent,
                    "largest_remaining": largest,
                    "neighbor_components": len(sizes),
                    "betweenness": btw,
                    "weight": CHOKE_WEIGHT,
                    "arithmetic": f"{crossing} * {dependent} * {CHOKE_WEIGHT} = {cost}",
                },
            }
        )

    choke_ids = {rid for f in findings if f["kind"] == "choke" for rid in f["region_ids"]}

    for rid in sorted(graph):
        region = _region_for(regions, rid)
        fan = outgoing.get(rid, 0)
        nlines = _lines(region)
        branches = _branch_count(region.get("body") or "")
        if fan < 1:
            continue
        high = nlines >= LINES_THRESHOLD or fan >= COMPLEX_FAN_OUT or branches >= COMPLEX_BRANCHES
        if not high:
            continue
        raw = (nlines / LINES_THRESHOLD) * fan * (1 + branches) * COMPLEX_WEIGHT
        cost = _num(raw)
        if cost <= 0:
            continue
        label = _label(region, rid)
        findings.append(
            {
                "id": _finding_id("complex", [rid]),
                "kind": "complex",
                "name": _short_name([label]),
                "region_ids": [rid],
                "regions": [label],
                "graph_node_ids": [rid],
                "cost": cost,
                "why": (
                    f"{nlines} lines, callee fan-out {fan}, {branches} branch-ish lines. "
                    "Structural weight is above the reporting bar."
                ),
                "breakdown": {
                    "lines": nlines,
                    "lines_threshold": LINES_THRESHOLD,
                    "fan_out": fan,
                    "branch_count": branches,
                    "weight": COMPLEX_WEIGHT,
                    "arithmetic": (
                        f"({nlines} / {LINES_THRESHOLD}) * {fan} * (1 + {branches}) * {COMPLEX_WEIGHT} = {cost}"
                    ),
                },
            }
        )

    for rid in sorted(graph):
        if rid in choke_ids:
            continue
        deg = incoming.get(rid, 0) + outgoing.get(rid, 0)
        if deg < HUB_MIN_DEGREE:
            continue
        cost = _num(deg * HUB_WEIGHT)
        region = _region_for(regions, rid)
        label = _label(region, rid)
        inn, out = incoming.get(rid, 0), outgoing.get(rid, 0)
        findings.append(
            {
                "id": _finding_id("hub", [rid]),
                "kind": "hub",
                "name": _short_name([label]),
                "region_ids": [rid],
                "regions": [label],
                "graph_node_ids": [rid],
                "cost": cost,
                "why": f"In-degree {inn} + out-degree {out} = {deg}. Call traffic concentrates here.",
                "breakdown": {
                    "in_degree": inn,
                    "out_degree": out,
                    "weight": HUB_WEIGHT,
                    "arithmetic": f"({inn} + {out}) * {HUB_WEIGHT} = {cost}",
                },
            }
        )

    orphans = []
    for rid in sorted(graph):
        if incoming.get(rid, 0) == 0 and outgoing.get(rid, 0) == 0:
            orphans.append(rid)
    omitted = max(0, len(orphans) - ORPHAN_CAP)
    for rid in orphans[:ORPHAN_CAP]:
        region = _region_for(regions, rid)
        label = _label(region, rid)
        cost = _num(ORPHAN_WEIGHT)
        findings.append(
            {
                "id": _finding_id("orphan", [rid]),
                "kind": "orphan",
                "name": _short_name([label]),
                "region_ids": [rid],
                "regions": [label],
                "graph_node_ids": [rid],
                "cost": cost,
                "why": "No callers and no callees in the indexed call graph.",
                "breakdown": {
                    "weight": ORPHAN_WEIGHT,
                    "arithmetic": f"{ORPHAN_WEIGHT} = {cost}",
                },
            }
        )

    return _shell(findings, regions_n=len(graph), edges_n=edges_n, omitted_orphans=omitted)


def _load_regions(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        """
        SELECT region_id, path, symbol, kind, start_line, end_line, body
        FROM source_regions
        WHERE kind != 'module'
        """
    ).fetchall()
    out: dict[str, dict] = {}
    for row in rows:
        out[row["region_id"]] = {
            "region_id": row["region_id"],
            "path": row["path"],
            "symbol": row["symbol"],
            "kind": row["kind"],
            "start_line": row["start_line"],
            "end_line": row["end_line"],
            "body": row["body"] or "",
        }
    return out


def _is_retry(finding: dict | None) -> bool:
    if not finding:
        return False
    blob = " ".join(finding.get("regions") or [])
    return any(marker in blob for marker in _RETRY_MARKERS)


def _pick_tangled(findings: list[dict]) -> dict | None:
    tangled = [f for f in findings if f["kind"] == "tangled"]
    for finding in tangled:
        if _is_retry(finding):
            return finding
    return tangled[0] if tangled else None


def _crossing(adj: dict[str, dict[str, float]], left: set[str], right: set[str]) -> int:
    seen: set[tuple[str, str]] = set()
    count = 0
    for u, vs in adj.items():
        for v in vs:
            if u == v:
                continue
            a, b = (u, v) if u < v else (v, u)
            if (a, b) in seen:
                continue
            if (a in left and b in right) or (a in right and b in left):
                seen.add((a, b))
                count += 1
    return count


def _worked_example(
    conn: sqlite3.Connection,
    graph: dict[str, list[str]],
    regions: dict[str, dict],
    report: dict,
) -> dict:
    """Arithmetic for the for_renewal / retry-cycle neighborhood, from measured facts."""
    findings = report.get("findings") or []
    tangled = _pick_tangled(findings)
    cycle_name = "retry-cycle SCC" if _is_retry(tangled) else "largest tangled SCC"
    lines: list[str] = []
    ids: list[str] = []

    if tangled is None:
        lines.append("No SCC of size greater than 1 was measured on the indexed call graph.")
    else:
        lines.append(f"tangled {cycle_name} ({tangled['name']}): {tangled['breakdown']['arithmetic']} debt-tokens")
        lines.append("SCC members: " + "; ".join(tangled["regions"]))
        ids.extend(tangled["graph_node_ids"])

    seed = resolve_region(conn, FAILING_SYMBOL, path_substr=FAILING_PATH)
    min_cut_weight = None
    neigh: set[str] = set()
    if seed is None:
        lines.append(
            f"{FAILING_PATH}::{FAILING_SYMBOL} is not in the index, so its span and fan-out were not measured."
        )
    else:
        seed_id = seed["region_id"]
        ids.append(seed_id)
        region = _region_for(regions, seed_id)
        nlines = _lines(region)
        fan = len([v for v in graph.get(seed_id, []) if v != seed_id])
        branches = _branch_count(region.get("body") or "")
        cost = _num((nlines / LINES_THRESHOLD) * fan * (1 + branches) * COMPLEX_WEIGHT)
        label = _label(region, seed_id)
        lines.append(
            f"{label}: lines {nlines}, fan_out {fan}, branch_count {branches} "
            f"→ ({nlines} / {LINES_THRESHOLD}) * {fan} * (1 + {branches}) * {COMPLEX_WEIGHT} = {cost} debt-tokens"
        )
        reported = any(f["kind"] == "complex" and seed_id in f["region_ids"] for f in findings)
        if reported:
            lines.append("for_renewal is above the complex-code bar, so that product is one of the findings.")
        else:
            lines.append(
                "for_renewal is below the complex-code bar. The product is shown here and is not added as a finding."
            )
        if tangled is not None:
            reached = set(reverse_bfs(graph, seed_id))
            overlap = sorted(set(tangled["region_ids"]) & reached)
            lines.append(
                f"reverse BFS from for_renewal reaches {len(overlap)} of "
                f"{len(tangled['region_ids'])} {cycle_name} regions"
            )
        info = symbol_neighborhood(conn, FAILING_SYMBOL, path_substr=FAILING_PATH)
        extra = set(info.get("neighborhood") or [])
        neigh = set(extra)
        neigh.add(seed_id)
        adj = undirected_component(graph, seed_id, extra)
        cut = stoer_wagner(adj)
        min_cut_weight = cut.get("weight")
        left, right = set(cut.get("left") or []), set(cut.get("right") or [])
        if seed_id in right and seed_id not in left:
            left, right = right, left
        crossing = _crossing(adj, left, right)
        lines.append(
            f"Stoer–Wagner min-cut of the for_renewal undirected neighborhood: "
            f"weight {min_cut_weight} on {len(adj)} nodes; "
            f"measured edges crossing the seed-side partition: {crossing}; "
            f"seed side {len(left)}, other side {len(right)}"
        )
        if min_cut_weight == 1 or min_cut_weight == 1.0:
            lines.append("That weight is 1, so the neighborhood min-cut is a single bridge.")

    if tangled is not None:
        neigh.update(tangled["region_ids"])
    chokes = [f for f in findings if f["kind"] == "choke" and set(f["region_ids"]) & neigh]
    lines.append(f"Articulation points in the {cycle_name} neighborhood: {len(chokes)}.")
    shown = chokes[:6]
    for finding in shown:
        lines.append(
            f"choke {finding['name']}: {finding['breakdown']['arithmetic']} debt-tokens. {finding['why']}"
        )
        ids.extend(finding["graph_node_ids"])
    if len(chokes) > len(shown):
        lines.append(f"Showing 6 of {len(chokes)} neighborhood choke points. The ranked list has the rest.")

    lines.append("Finding costs above are already included in the repo total. This note does not add a second charge.")
    return {
        "focus": "for_renewal / retry-cycle",
        "arithmetic": lines,
        "graph_node_ids": list(dict.fromkeys(ids)),
        "min_cut_weight": min_cut_weight,
    }


def debt_from_conn(conn: sqlite3.Connection) -> dict:
    graph = call_graph(conn)
    regions = _load_regions(conn)
    report = analyze_debt(graph, regions)
    report["worked_example"] = _worked_example(conn, graph, regions, report)
    clones = clones_from_conn(conn)
    redundant = int(clones["total_redundant_cost"])
    structural = report["total_debt"]
    report["structural_debt"] = structural
    report["redundant"] = {
        "label": "redundant functions",
        "cost": redundant,
        "savings": clones["total_savings"],
        "clusters": clones["cluster_count"],
        "formula": clones["formula"],
        "note": (
            "TraceWeaver AST clone cost. Separate from choke, tangled, and complex findings."
        ),
    }
    report["total_debt"] = _num(structural + redundant)
    report["dollar_equivalent"] = report["total_debt"]
    formula = dict(report.get("formula") or {})
    formula["redundant_cost"] = "(copies - 1) * max(1, len(source)//4)"
    formula["redundant_rule"] = (
        "Same-shape functions after AST normalization, plus near clones with "
        "node-type trigram Jaccard >= 0.82. Priced once, beside choke, tangled, and complex."
    )
    formula["total"] = "sum of finding costs + redundant_cost"
    report["formula"] = formula
    return report


def debt_for_runtime(runtime) -> dict:
    n = runtime.conn.execute("SELECT COUNT(*) c FROM source_regions").fetchone()["c"]
    if n == 0:
        runtime.reindex()
    return debt_from_conn(runtime.conn)
