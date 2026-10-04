"""Tarjan strongly connected components, reverse BFS, and Stoer–Wagner min-cut."""

from __future__ import annotations

from collections import deque


def tarjan_scc(graph: dict[str, list[str]]) -> list[list[str]]:
    """Return strongly connected components of a directed graph.

    ``graph`` maps each node to its successors. Every node that should appear
    in the result must be a key, even if its successor list is empty.
    Components are returned in reverse topological order of the condensation.
    """
    index = 0
    stack: list[str] = []
    onstack: set[str] = set()
    indices: dict[str, int] = {}
    low: dict[str, int] = {}
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        low[v] = index
        index += 1
        stack.append(v)
        onstack.add(v)
        for w in graph.get(v, []):
            if w not in indices:
                strongconnect(w)
                low[v] = min(low[v], low[w])
            elif w in onstack:
                low[v] = min(low[v], indices[w])
        if low[v] == indices[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                onstack.discard(w)
                comp.append(w)
                if w == v:
                    break
            sccs.append(comp)

    for v in list(graph):
        if v not in indices:
            strongconnect(v)
    return sccs


def _incoming(graph: dict[str, list[str]], start: str) -> dict[str, list[str]]:
    incoming: dict[str, list[str]] = {n: [] for n in graph}
    incoming.setdefault(start, [])
    for u, vs in graph.items():
        incoming.setdefault(u, [])
        for v in vs:
            incoming.setdefault(v, [])
            incoming[v].append(u)
    for u in incoming:
        incoming[u] = sorted(set(incoming[u]))
    return incoming


def reverse_bfs(graph: dict[str, list[str]], start: str) -> list[str]:
    """BFS on reversed edges. Dequeue order, ``start`` first.

    Successors are expanded in sorted order so the visit order is stable.
    """
    incoming = _incoming(graph, start)
    order: list[str] = []
    seen = {start}
    q: deque[str] = deque([start])
    while q:
        u = q.popleft()
        order.append(u)
        for v in incoming.get(u, []):
            if v not in seen:
                seen.add(v)
                q.append(v)
    return order


def reverse_bfs_depths(graph: dict[str, list[str]], start: str) -> list[tuple[str, int]]:
    """Same visit order as ``reverse_bfs``, with the depth of each node."""
    incoming = _incoming(graph, start)
    order: list[tuple[str, int]] = []
    dist = {start: 0}
    q: deque[str] = deque([start])
    while q:
        u = q.popleft()
        order.append((u, dist[u]))
        for v in incoming.get(u, []):
            if v not in dist:
                dist[v] = dist[u] + 1
                q.append(v)
    return order


def shortest_reverse_path(graph: dict[str, list[str]], start: str, goal: str) -> list[str] | None:
    """Shortest path from ``start`` along reversed edges, inclusive.

    ``None`` when ``goal`` is not reachable. ``start`` to itself is ``[start]``.
    """
    if start == goal:
        return [start]
    incoming = _incoming(graph, start)
    parent: dict[str, str | None] = {start: None}
    q: deque[str] = deque([start])
    while q:
        u = q.popleft()
        for v in incoming.get(u, []):
            if v in parent:
                continue
            parent[v] = u
            if v == goal:
                path = [goal]
                cur: str | None = u
                while cur is not None:
                    path.append(cur)
                    cur = parent[cur]
                path.reverse()
                return path
            q.append(v)
    return None


def stoer_wagner(adj: dict[str, dict[str, float]]) -> dict:
    """Global minimum cut of an undirected weighted graph (Stoer–Wagner).

    ``adj[u][v]`` is the weight of the undirected edge. One direction is enough.
    Returns ``weight`` plus the two sides of a cut that achieves it.
    A graph with fewer than 2 nodes has weight ``None``.
    """
    nodes = sorted({n for n in adj} | {v for vs in adj.values() for v in vs})
    if len(nodes) < 2:
        return {"weight": None, "left": list(nodes), "right": []}

    link: dict[str, dict[str, float]] = {u: {} for u in nodes}
    for u, vs in adj.items():
        for v, wt in vs.items():
            if u == v:
                continue
            weight = float(wt)
            link.setdefault(u, {})
            link.setdefault(v, {})
            link[u][v] = max(link[u].get(v, 0.0), weight)
            link[v][u] = max(link[v].get(u, 0.0), weight)

    current = sorted(link)
    membership: dict[str, set[str]] = {n: {n} for n in current}
    best_value = float("inf")
    best_side: set[str] = set()

    def tightness(group: list[str], v: str) -> float:
        return sum(link[v].get(a, 0.0) for a in group)

    while len(current) > 1:
        start = current[0]
        added = [start]
        remaining = set(current[1:])
        while remaining:
            v = max(remaining, key=lambda x: (tightness(added, x), x))
            added.append(v)
            remaining.remove(v)
        s, t = added[-2], added[-1]
        cut_value = tightness(added[:-1], t)
        if cut_value < best_value:
            best_value = cut_value
            best_side = set(membership[t])
        for v, wt in list(link.get(t, {}).items()):
            if v == s or v == t:
                continue
            link[s][v] = link[s].get(v, 0.0) + wt
            link[v][s] = link[s][v]
            link[v].pop(t, None)
        link.pop(t, None)
        link[s].pop(t, None)
        membership[s] = membership[s] | membership[t]
        del membership[t]
        current.remove(t)

    universe = set(nodes)
    left = set(best_side)
    if not left or left == universe:
        # A phase always records a proper side when |V| >= 2. Keep a stable split.
        lone = nodes[-1]
        left = {lone}
    right = universe - left
    weight: float | None
    weight = None if best_value == float("inf") else float(best_value)
    return {"weight": weight, "left": sorted(left), "right": sorted(right)}
