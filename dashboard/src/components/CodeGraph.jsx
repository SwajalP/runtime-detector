import React, { useMemo, useState } from "react";

const LEVEL_COLOR = { L0: "#3ee0b0", L1: "#7aa2ff", L2: "#f5c542", backing: "#8b97a8" };
const EDGE_COLOR = {
  call: "#7aa2ff",
  exec: "#ff7a45",
  co_access: "#3ee0b0",
  co_access_history: "#5a6b7d",
  successor: "#ff6b6b",
};

function layout(nodes, width, height) {
  const packages = [];
  const byPkg = new Map();
  for (const n of nodes) {
    const pkg = n.package || ".";
    if (!byPkg.has(pkg)) {
      byPkg.set(pkg, []);
      packages.push(pkg);
    }
    byPkg.get(pkg).push(n);
  }
  const colW = width / Math.max(packages.length, 1);
  const pos = {};
  packages.forEach((pkg, i) => {
    const col = byPkg.get(pkg);
    col.sort((a, b) => (a.start_line || 0) - (b.start_line || 0));
    const rowH = height / (col.length + 1);
    col.forEach((n, j) => {
      pos[n.id] = { x: colW * (i + 0.5), y: rowH * (j + 1), pkg };
    });
  });
  return { pos, packages, colW };
}

export default function CodeGraph({ graph }) {
  const [hover, setHover] = useState(null);
  const width = 920;
  const height = 420;
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  const { pos } = useMemo(() => layout(nodes, width, height), [nodes, width, height]);

  if (!nodes.length) {
    return <p className="empty">Index a repo and run a task to populate the knowledge graph.</p>;
  }

  return (
    <div className="graph-wrap">
      <svg viewBox={`0 0 ${width} ${height}`} className="graph-svg" role="img" aria-label="Code knowledge graph">
        {edges.map((e, i) => {
          const a = pos[e.source];
          const b = pos[e.target];
          if (!a || !b) return null;
          return (
            <line
              key={`${e.type}-${e.source}-${e.target}-${i}`}
              x1={a.x}
              y1={a.y}
              x2={b.x}
              y2={b.y}
              stroke={EDGE_COLOR[e.type] || "#35508a"}
              strokeWidth={e.type === "exec" ? 2 : 1}
              opacity={e.type === "co_access_history" ? 0.35 : 0.7}
            />
          );
        })}
        {nodes.map((n) => {
          const p = pos[n.id];
          if (!p) return null;
          const r = n.frame || n.pinned ? 8 : n.in_bundle ? 6 : 4;
          return (
            <circle
              key={n.id}
              cx={p.x}
              cy={p.y}
              r={r}
              fill={n.stale ? "#ff6b6b" : LEVEL_COLOR[n.level] || "#8b97a8"}
              stroke={n.joined ? "#fff" : "transparent"}
              strokeWidth={n.joined ? 1.5 : 0}
              onMouseEnter={() => setHover(n)}
              onMouseLeave={() => setHover(null)}
            />
          );
        })}
      </svg>
      <div className="legend">
        {Object.entries(LEVEL_COLOR).map(([k, c]) => (
          <span key={k}><i style={{ background: c }} />{k}</span>
        ))}
        <span><i style={{ background: EDGE_COLOR.exec }} />exec</span>
        <span><i style={{ background: EDGE_COLOR.call }} />call</span>
      </div>
      {hover && (
        <div className="graph-tip">
          <strong>{hover.symbol}</strong> {hover.path}:{hover.start_line}-{hover.end_line}
          <div>{hover.level} · score {hover.score ?? "—"} · {hover.explanation || ""}</div>
        </div>
      )}
    </div>
  );
}
