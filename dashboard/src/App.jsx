import React, { useEffect, useState } from "react";
import CodeGraph from "./components/CodeGraph.jsx";
import MemoryHierarchy from "./components/MemoryHierarchy.jsx";

function AuditPanel({ audit }) {
  if (!audit) return <p className="muted">audit unavailable</p>;
  const cut = audit.min_cut;
  const bfs = audit.reverse_bfs;
  const sccs = audit.large_sccs || [];
  const live = !audit.fixture && (cut || bfs || sccs.length);
  if (!live) {
    return (
      <>
        <p className="muted">{audit.label || audit.disclaimer || "fixture JSON — not a live measurement"}</p>
        <pre>{JSON.stringify(audit, null, 2)}</pre>
      </>
    );
  }
  return (
    <>
      <p className="muted">{audit.disclaimer || "structural audit computed from the index"}</p>
      <div className="audit-grid">
        <div className="audit-col">
          <h3>Tarjan SCC</h3>
          <p className="muted">largest {audit.largest_scc_size} · {audit.directed_edges} edges</p>
          <ul>
            {sccs.length ? sccs.map((s) => (
              <li key={s.size + (s.regions || []).join()}>size {s.size}: {(s.regions || []).join("; ")}</li>
            )) : <li>largest SCC size {audit.largest_scc_size}</li>}
          </ul>
        </div>
        <div className="audit-col">
          <h3>Stoer–Wagner partition</h3>
          <p className="muted">weight {cut?.weight} · seed side stays with {cut?.around}</p>
          <p>{cut?.note}</p>
          <p>Seed side</p>
          <ul>{(cut?.seed_side || []).map((s) => <li key={s}>{s}</li>)}</ul>
          <p>Other side</p>
          <ul>{(cut?.other_side || []).map((s) => <li key={s}>{s}</li>)}</ul>
        </div>
        <div className="audit-col">
          <h3>Reverse BFS depths</h3>
          <p className="muted">depth {bfs?.depth} from {bfs?.failing_test} to {bfs?.seed}</p>
          <ol>
            {(bfs?.depths || []).map((d) => (
              <li key={d.region_id || d.region}>depth {d.depth}: {d.region}</li>
            ))}
          </ol>
        </div>
      </div>
      <pre>{JSON.stringify(audit, null, 2)}</pre>
    </>
  );
}

export default function App() {
  const [state, setState] = useState({ events: [], working_set: [], metrics: {}, heat: {}, execution_path: [] });
  const [graph, setGraph] = useState({ nodes: [], edges: [] });
  const [hierarchy, setHierarchy] = useState(null);
  const [ab, setAb] = useState("");
  const [audit, setAudit] = useState(null);

  async function refresh() {
    const [s, g, h, a] = await Promise.all([
      fetch("/api/state").then((r) => r.json()),
      fetch("/api/graph").then((r) => r.json()),
      fetch("/api/graph/hierarchy").then((r) => r.json()),
      fetch("/api/audit").then((r) => r.json()).catch(() => null),
    ]);
    setState(s);
    setGraph(g);
    setHierarchy(h);
    setAudit(a);
  }

  useEffect(() => {
    refresh();
    const es = new EventSource("/api/stream");
    es.onmessage = (msg) => {
      try {
        const data = JSON.parse(msg.data);
        if (data.type === "ping") return;
      } catch {
        return;
      }
      refresh();
    };
    return () => es.close();
  }, []);

  async function run(condition) {
    setAb("running " + condition + "…");
    const r = await fetch(`/api/eval/run?task_id=renewal-discount&condition=${condition}`, { method: "POST" });
    setAb(JSON.stringify(await r.json(), null, 2));
    refresh();
  }

  async function compare() {
    setAb("comparing…");
    const r = await fetch("/api/eval/compare?task_id=renewal-discount", { method: "POST" });
    setAb(JSON.stringify(await r.json(), null, 2));
    refresh();
  }

  const m = state.metrics || {};
  const heat = Object.entries(state.heat || {}).sort((a, b) => b[1] - a[1]).slice(0, 12);
  const max = heat[0]?.[1] || 1;
  const ev = state.last_eval;
  const evalLine = ev
    ? `eval ${ev.n_tasks}×${ev.repeats || 1}: baseline ${ev.baseline?.success}/${ev.baseline?.n} vs LEDGER ${ev.ledger?.success}/${ev.ledger?.n} · tokens ${ev.change_pct?.repo_tokens ?? "—"}%`
    : "";

  return (
    <div>
      <header>
        <div>
          <strong>LEDGER RUNTIME</strong>
          <span> context memory hierarchy · dual-trace controller</span>
          {state.replay ? <span> · REPLAY — recorded events, not a live model</span> : null}
        </div>
        <div className="toolbar">
          <button onClick={() => run("ledger")}>Run LEDGER</button>
          <button className="baseline" onClick={() => run("baseline")}>Run baseline</button>
          <button onClick={compare}>A/B compare</button>
        </div>
      </header>
      <div className="grid">
        <div className="card metrics">
          {[
            ["session", (state.session_id || "—").slice(0, 16)],
            ["tool calls", m.repo_tool_calls ?? "—"],
            ["repo tokens", m.repo_tokens ?? "—"],
            ["hit rate", m.hit_rate != null ? `${Math.round(m.hit_rate * 100)}%` : "—"],
            ["invalidations", m.invalidations ?? 0],
            ["stale", m.stale_entries ?? 0],
          ].map(([k, v]) => (
            <div className="metric" key={k}>
              <label>{k}</label>
              <b>{v}</b>
            </div>
          ))}
        </div>
        <div className="card" style={{ gridRow: "span 2" }}>
          <h2>Agent + program timeline</h2>
          {(state.events || []).slice(-80).reverse().map((e) => (
            <div className="evt" key={e.event_id}>
              <span>{e.turn}</span>
              <span className={"op-" + e.operation}>{e.operation}</span>
              <span>{String(e.query || e.source || "").slice(0, 88)}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Joined execution path</h2>
          {(state.execution_path || []).length === 0 && (
            <span className="empty">Run a failing test to light this up.</span>
          )}
          {(state.execution_path || []).map((p) => (
            <div className={"path " + (p.stale ? "stale" : "")} key={p.region_id}>
              {p.symbol || p.path}{" "}
              <span className="muted">x={Number(p.execution_score || 0).toFixed(2)}</span>
            </div>
          ))}
        </div>
        <div className="card">
          <h2>Context temperature</h2>
          {heat.map(([p, v]) => (
            <div className="heatrow" key={p}>
              <div className="bar" style={{ width: `${Math.max(8, (v / max) * 100)}%` }} />
              <span>{p}</span>
            </div>
          ))}
        </div>
        <div className="card" style={{ gridColumn: "2 / -1" }}>
          <h2>Active working set</h2>
          <table>
            <thead>
              <tr>
                <th>region</th>
                <th>exec</th>
                <th>freq</th>
                <th>fresh</th>
                <th>why</th>
              </tr>
            </thead>
            <tbody>
              {(state.working_set || []).slice(0, 18).map((w) => (
                <tr key={w.region_id}>
                  <td>{w.symbol || w.path || w.region_id}</td>
                  <td>{Number(w.execution_score || 0).toFixed(2)}</td>
                  <td>{Number(w.access_frequency || 0).toFixed(1)}</td>
                  <td className={w.stale ? "stale" : ""}>{w.stale ? "STALE" : "fresh"}</td>
                  <td>{(w.explanation || "").slice(0, 80)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Code knowledge graph</h2>
          <CodeGraph graph={graph} />
        </div>
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Memory hierarchy · L0 / L1 / L2 / backing</h2>
          <MemoryHierarchy hierarchy={hierarchy} />
        </div>
        <div className="card" style={{ gridColumn: "1 / -1" }}>
          <h2>Structural audit · SCC / min-cut / clones</h2>
          <AuditPanel audit={audit} />
        </div>
        <div className="card compare">
          <h2>Last A/B</h2>
          {evalLine && <p className="muted">{evalLine}</p>}
          <pre>{ab}</pre>
        </div>
      </div>
    </div>
  );
}
