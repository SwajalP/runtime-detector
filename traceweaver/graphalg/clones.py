"""AST clone detection for redundant functions.

Fingerprints keep node types and control-flow shape, replace local names with
canonical ids in first-seen order, and drop docstrings and line numbers.
Exact fingerprint matches are clones. Node-type trigram Jaccard >= 0.82 marks
a near clone. This is structural similarity, not the vector index.
"""

from __future__ import annotations

import ast
import hashlib
import textwrap
from collections import defaultdict

NEAR_THRESHOLD = 0.82
MIN_STATEMENTS = 3
NGRAM_N = 3
FORMULA = "redundant_cost = (copies - 1) * max(1, len(source)//4)"
EXACT_NOTE = "The bodies match after AST normalization."
NEAR_NOTE = "The bodies nearly match after AST normalization."

_SKIP_FIELDS = {"lineno", "col_offset", "end_lineno", "end_col_offset", "type_comment"}


def _is_docstring(stmt: ast.stmt) -> bool:
    return (
        isinstance(stmt, ast.Expr)
        and isinstance(stmt.value, ast.Constant)
        and isinstance(stmt.value.value, str)
    )


def _statement_count(fn: ast.AST) -> int:
    body = list(getattr(fn, "body", []) or [])
    if body and _is_docstring(body[0]):
        body = body[1:]
    count = 0
    for stmt in body:
        count += sum(1 for node in ast.walk(stmt) if isinstance(node, ast.stmt))
    return count


def _children(node: ast.AST) -> list[ast.AST]:
    kids: list[ast.AST] = []
    for field, value in ast.iter_fields(node):
        if field in _SKIP_FIELDS:
            continue
        if field == "body" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = list(value or [])
            if body and _is_docstring(body[0]):
                body = body[1:]
            kids.extend(body)
            continue
        if isinstance(value, ast.AST):
            kids.append(value)
        elif isinstance(value, list):
            kids.extend(item for item in value if isinstance(item, ast.AST))
    return kids


def _canon(names: dict[str, str], raw: str | None) -> str | None:
    if not raw:
        return None
    if raw not in names:
        names[raw] = f"n{len(names)}"
    return names[raw]


def _own_ids(node: ast.AST, names: dict[str, str]) -> list[str]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        token = _canon(names, node.name)
        return [token] if token else []
    if isinstance(node, ast.arg):
        token = _canon(names, node.arg)
        return [token] if token else []
    if isinstance(node, ast.Name):
        token = _canon(names, node.id)
        return [token] if token else []
    if isinstance(node, ast.Attribute):
        token = _canon(names, node.attr)
        return [token] if token else []
    if isinstance(node, ast.keyword):
        token = _canon(names, node.arg)
        return [token] if token else []
    if isinstance(node, ast.ExceptHandler):
        token = _canon(names, node.name)
        return [token] if token else []
    if isinstance(node, ast.alias):
        out = []
        token = _canon(names, node.name)
        if token:
            out.append(token)
        alias = _canon(names, node.asname)
        if alias:
            out.append(alias)
        return out
    if isinstance(node, (ast.Global, ast.Nonlocal)):
        return [token for name in node.names if (token := _canon(names, name))]
    if isinstance(node, ast.Constant):
        return [repr(node.value)]
    if type(node).__name__.startswith("Match"):
        token = _canon(names, getattr(node, "name", None))
        return [token] if token else []
    return []


def _fingerprint(fn: ast.AST) -> tuple[str, list[str]]:
    names: dict[str, str] = {}
    parts: list[str] = []
    types: list[str] = []

    def walk(node: ast.AST) -> None:
        types.append(type(node).__name__)
        kids = _children(node)
        ids = _own_ids(node, names)
        token = f"{type(node).__name__}#{len(kids)}"
        if ids:
            token += ":" + ",".join(ids)
        parts.append(token)
        for kid in kids:
            walk(kid)

    walk(fn)
    return "\n".join(parts), types


def _ngrams(types: list[str], n: int = NGRAM_N) -> frozenset[tuple[str, ...]]:
    if len(types) < n:
        return frozenset()
    return frozenset(tuple(types[i : i + n]) for i in range(len(types) - n + 1))


def _jaccard(left: frozenset, right: frozenset) -> float:
    if not left and not right:
        return 1.0
    if not left or not right:
        return 0.0
    inter = len(left & right)
    return inter / (len(left) + len(right) - inter)


def _parse_function(source: str, name: str | None) -> ast.AST | None:
    try:
        tree = ast.parse(textwrap.dedent(source))
    except SyntaxError:
        return None
    funcs = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    short = name.rsplit(".", 1)[-1] if name else None
    if short:
        matched = [node for node in funcs if node.name == short]
        if len(matched) == 1:
            return matched[0]
    if len(funcs) == 1:
        return funcs[0]
    return None


def _empty_report() -> dict:
    return {
        "kind": "ast-clones",
        "unit": "debt-tokens",
        "formula": FORMULA,
        "near_threshold": NEAR_THRESHOLD,
        "min_statements": MIN_STATEMENTS,
        "total_redundant_cost": 0,
        "total_savings": 0,
        "cluster_count": 0,
        "functions_compared": 0,
        "clusters": [],
    }


def analyze_clones(functions: list[dict]) -> dict:
    """Cluster function records that share a normalized AST shape.

    Each record uses ``source`` or ``body``, plus ``name`` or ``symbol`` and ``path``.
    """
    prepared: list[dict] = []
    for fn in functions:
        source = fn.get("source")
        if source is None:
            source = fn.get("body") or ""
        name = fn.get("name") or fn.get("symbol") or "<function>"
        path = fn.get("path") or ""
        parsed = _parse_function(source, name)
        if parsed is None or _statement_count(parsed) < MIN_STATEMENTS:
            continue
        digest, types = _fingerprint(parsed)
        prepared.append(
            {
                "region_id": fn.get("region_id") or f"{path}::{name}",
                "path": path,
                "name": name,
                "symbol": fn.get("symbol") or name,
                "kind": fn.get("kind") or "function",
                "source": source,
                "digest": digest,
                "grams": _ngrams(types),
                "estimated_tokens": max(1, len(source) // 4),
            }
        )

    if not prepared:
        return _empty_report()

    parent = list(range(len(prepared)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left == root_right:
            return
        if root_left < root_right:
            parent[root_right] = root_left
        else:
            parent[root_left] = root_right

    by_digest: dict[str, list[int]] = defaultdict(list)
    for index, item in enumerate(prepared):
        by_digest[item["digest"]].append(index)
    for group in by_digest.values():
        head = group[0]
        for other in group[1:]:
            union(head, other)

    near_edges: list[tuple[int, int, float]] = []
    for left in range(len(prepared)):
        for right in range(left + 1, len(prepared)):
            if prepared[left]["digest"] == prepared[right]["digest"]:
                continue
            score = _jaccard(prepared[left]["grams"], prepared[right]["grams"])
            if score >= NEAR_THRESHOLD:
                union(left, right)
                near_edges.append((left, right, score))

    groups: dict[int, list[int]] = defaultdict(list)
    for index in range(len(prepared)):
        groups[find(index)].append(index)

    clusters: list[dict] = []
    for indexes in groups.values():
        if len(indexes) < 2:
            continue
        members = [prepared[index] for index in indexes]
        members.sort(key=lambda item: (item["path"], item["name"], item["region_id"]))
        kept = members[0]
        copies = len(members)
        estimated = kept["estimated_tokens"]
        cost = (copies - 1) * estimated
        fingerprints = {item["digest"] for item in members}
        member_ids = {item["region_id"] for item in members}
        if len(fingerprints) == 1:
            match = "exact"
            similarity: int | float = 1
            note = EXACT_NOTE
        else:
            match = "near"
            scores = [
                score
                for left, right, score in near_edges
                if prepared[left]["region_id"] in member_ids and prepared[right]["region_id"] in member_ids
            ]
            similarity = round(min(scores), 4) if scores else NEAR_THRESHOLD
            note = NEAR_NOTE
        identity = "|".join(f"{item['path']}::{item['name']}::{item['region_id']}" for item in members)
        clusters.append(
            {
                "id": hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12],
                "match": match,
                "similarity": similarity,
                "copies": copies,
                "estimated_tokens": estimated,
                "redundant_cost": cost,
                "savings": cost,
                "note": note,
                "members": [
                    {
                        "region_id": item["region_id"],
                        "name": item["name"],
                        "symbol": item["symbol"],
                        "path": item["path"],
                        "kind": item["kind"],
                        "estimated_tokens": item["estimated_tokens"],
                    }
                    for item in members
                ],
            }
        )

    clusters.sort(key=lambda item: (-item["redundant_cost"], -item["copies"], item["id"]))
    total = sum(item["redundant_cost"] for item in clusters)
    report = _empty_report()
    report["total_redundant_cost"] = total
    report["total_savings"] = total
    report["cluster_count"] = len(clusters)
    report["functions_compared"] = len(prepared)
    report["clusters"] = clusters
    return report


def clones_from_conn(conn) -> dict:
    rows = conn.execute(
        """
        SELECT region_id, path, symbol, kind, body
        FROM source_regions
        WHERE kind IN ('function', 'method')
        """
    ).fetchall()
    functions = [
        {
            "region_id": row["region_id"],
            "path": row["path"],
            "name": row["symbol"],
            "symbol": row["symbol"],
            "kind": row["kind"],
            "source": row["body"] or "",
        }
        for row in rows
    ]
    return analyze_clones(functions)


def clones_for_runtime(runtime) -> dict:
    count = runtime.conn.execute("SELECT COUNT(*) c FROM source_regions").fetchone()["c"]
    if count == 0:
        runtime.reindex()
    return clones_from_conn(runtime.conn)
