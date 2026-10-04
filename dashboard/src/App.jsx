import React, { useEffect, useState } from "react";

export default function App() {
  const [state, setState] = useState({ events: [], working_set: [], metrics: {}, heat: {}, execution_path: [] });
  const [ab, setAb] = useState("");

  async function refresh() {
    const s = await (await fetch("/api/state")).json();
    setState(s);
  }

  useEffect(() => {
    refresh();
    const es = new EventSource("/api/stream");
    es.onmessage = () => refresh();
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

  return (
    <div>
      <header>
        <div>
          <strong>LEDGER RUNTIME</strong>
          <span> context memory hierarchy · dual-trace controller</span>
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
            <span style={{ color: "#8b97a8" }}>Run a failing test to light this up.</span>
          )}
          {(state.execution_path || []).map((p) => (
            <div className={"path " + (p.stale ? "stale" : "")} key={p.region_id}>
              {p.symbol || p.path}{" "}
              <span style={{ color: "#8b97a8" }}>x={Number(p.execution_score || 0).toFixed(2)}</span>
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
        <div className="card compare">
          <h2>Last A/B</h2>
          <pre>{ab}</pre>
        </div>
      </div>
    </div>
  );
}
