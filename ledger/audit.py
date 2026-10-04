"""Structural audit of the indexed call graph.

Large SCCs (Tarjan), a Stoer–Wagner min-cut around the renewal failing path,
clone clusters, and lexical traps. This is not a quality score and it does
not claim to beat SonarQube or any other analyzer. A comparison table, when
present, is loaded from a labeled fixture.
"""

from __future__ import annotations

import hashlib
import json
import re

from ledger.config import LedgerConfig
from ledger.fixtures import load_fixture
from ledger.graphalg.algorithms import stoer_wagner, tarjan_scc
from ledger.graphalg.apply import call_graph, resolve_region, symbol_neighborhood, undirected_component
from ledger.runtime import LedgerRuntime

_COMMENT = re.compile(r"#.*")
_SPACE = re.compile(r"\s+")
_TRAP_PREFIXES = ("shop/legacy/", "shop/promotions/", "shop/catalog/", "shop/notifications/")
_TRAP_TOKENS = ("renewal", "discount", "loyalty")
FAILING_SYMBOL = "for_renewal"
FAILING_PATH = "shop/billing/discount_policy.py"


def _norm_body(body: str) -> str:
    text = _COMMENT.sub("", body or "")
    return _SPACE.sub(" ", text).strip().lower()


def _labels(conn, ids: list[str], limit: int = 40) -> list[str]:
    out = []
    for rid in ids[:limit]:
        row = conn.execute(
            "SELECT path, symbol FROM source_regions WHERE region_id = ?", (rid,)
        ).fetchone()
        if row and row["symbol"]:
            out.append(f"{row['path']}::{row['symbol']}")
        elif row:
            out.append(str(row["path"]))
        else:
            out.append(rid)
    return out


def _large_sccs(conn, graph: dict[str, list[str]]) -> list[dict]:
    found = []
    for comp in tarjan_scc(graph):
        if len(comp) < 2:
            continue
        found.append({"size": len(comp), "regions": _labels(conn, sorted(comp))})
    found.sort(key=lambda item: item["size"], reverse=True)
    return found


def _min_cut_around_failing(conn, graph: dict[str, list[str]]) -> dict:
    row = resolve_region(conn, FAILING_SYMBOL, path_substr=FAILING_PATH)
    if row is None:
        return {
            "algorithm": "stoer-wagner",
            "around": f"{FAILING_PATH}::{FAILING_SYMBOL}",
            "weight": None,
            "note": "failing-path symbol is not in the index",
            "seed_side": [],
            "other_side": [],
        }
    seed = row["region_id"]
    info = symbol_neighborhood(conn, FAILING_SYMBOL, path_substr=FAILING_PATH)
    extra = set(info.get("neighborhood") or [])
    extra.add(seed)
    adj = undirected_component(graph, seed, extra)
    cut = stoer_wagner(adj)
    left, right = set(cut["left"]), set(cut["right"])
    if seed in right and seed not in left:
        left, right = right, left
    return {
        "algorithm": "stoer-wagner",
        "around": f"{row['path']}::{row['symbol']}",
        "weight": cut.get("weight"),
        "nodes": len(adj),
        "seed_side": _labels(conn, sorted(left)),
        "other_side": _labels(conn, sorted(right)),
        "note": "Global min-cut of the undirected caller/callee neighborhood of the renewal failing path.",
    }


def _clone_clusters(conn) -> list[dict]:
    groups: dict[str, list[dict]] = {}
    rows = conn.execute(
        """
        SELECT region_id, path, symbol, kind, body FROM source_regions
        WHERE kind IN ('function', 'method')
        """
    ).fetchall()
    for row in rows:
        norm = _norm_body(row["body"] or "")
        if len(norm) < 48:
            continue
        digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]
        groups.setdefault(digest, []).append(
            {"path": row["path"], "symbol": row["symbol"], "kind": row["kind"]}
        )
    clusters = []
    for digest, members in groups.items():
        if len(members) < 2:
            continue
        clusters.append({"hash": digest, "size": len(members), "regions": members})
    clusters.sort(key=lambda item: item["size"], reverse=True)
    return clusters


def _lexical_traps(conn) -> list[dict]:
    traps = []
    rows = conn.execute(
        """
        SELECT path, symbol, kind, signature, body FROM source_regions
        WHERE kind != 'module'
        """
    ).fetchall()
    for row in rows:
        if row["path"] == FAILING_PATH and row["symbol"] == FAILING_SYMBOL:
            continue
        hay = f"{row['path']} {row['symbol']} {row['signature'] or ''} {row['body'] or ''}".lower()
        hits = [tok for tok in _TRAP_TOKENS if tok in hay]
        if len(hits) < 2:
            continue
        in_trap_dir = (row["path"] or "").startswith(_TRAP_PREFIXES)
        mentions_policy = "discount_policy" in (row["path"] or "")
        if not in_trap_dir and mentions_policy:
            continue
        if not in_trap_dir and "renewal" not in hay:
            continue
        traps.append(
            {
                "path": row["path"],
                "symbol": row["symbol"],
                "kind": row["kind"],
                "tokens": hits,
                "why": "lexical overlap with renewal/discount/loyalty outside shop/billing/discount_policy.py::for_renewal",
            }
        )
    traps.sort(key=lambda item: (item["path"], item["symbol"] or ""))
    return traps


def build_audit(cfg: LedgerConfig) -> dict:
    rt = LedgerRuntime(cfg)
    n = rt.conn.execute("SELECT COUNT(*) c FROM source_regions").fetchone()["c"]
    if n == 0:
        rt.reindex()
    else:
        rt.sync()
    graph = call_graph(rt.conn)
    sccs = tarjan_scc(graph)
    comparison = load_fixture("audit_comparison.json")
    comparison["fixture"] = True
    return {
        "kind": "structural",
        "fixture": False,
        "disclaimer": (
            "Structural audit of the indexed call graph: large SCCs, a min-cut around the "
            "failing renewal path, clone clusters, and lexical traps. Not a SonarQube score "
            "and not a claim to beat any static analyzer."
        ),
        "repo": str(cfg.repo_root),
        "regions": len(graph),
        "largest_scc_size": max((len(comp) for comp in sccs), default=0),
        "large_sccs": _large_sccs(rt.conn, graph),
        "min_cut": _min_cut_around_failing(rt.conn, graph),
        "clone_clusters": _clone_clusters(rt.conn),
        "lexical_traps": _lexical_traps(rt.conn),
        "fixture_comparison": comparison,
        "mailbox_status": load_fixture("mailbox_status.json"),
    }


def write_audit(cfg: LedgerConfig) -> dict:
    report = build_audit(cfg)
    cfg.ensure_dirs()
    path = cfg.ledger_dir / "last_audit.json"
    path.write_text(json.dumps(report, indent=2) + "\n")
    report = dict(report)
    report["written"] = str(path)
    return report


def load_audit_for_dashboard(cfg: LedgerConfig) -> dict:
    path = cfg.ledger_dir / "last_audit.json"
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["loaded_from"] = str(path)
            return data
        except json.JSONDecodeError:
            pass
    fixture = load_fixture("audit_comparison.json")
    return {
        "kind": "structural",
        "fixture": True,
        "label": "fixture — run `ledger audit` to compute SCC, min-cut, clones, and lexical traps from the index",
        "disclaimer": fixture.get("label"),
        "fixture_comparison": fixture,
        "large_sccs": [],
        "min_cut": None,
        "clone_clusters": [],
        "lexical_traps": [],
    }
