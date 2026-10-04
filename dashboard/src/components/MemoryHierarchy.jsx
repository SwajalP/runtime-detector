import React from "react";

const ORDER = ["L0", "L1", "L2", "backing"];

export default function MemoryHierarchy({ hierarchy }) {
  const levels = hierarchy?.levels || {};
  const stats = hierarchy?.stats || {};
  const log = hierarchy?.log || [];
  if (!hierarchy) {
    return <p className="empty">No hierarchy payload yet — run a session.</p>;
  }
  return (
    <div>
      <div className="hier-stats">
        <span>budget {stats.budget ?? "—"}</span>
        <span>used {stats.bundle_tokens ?? "—"}</span>
        <span>hit {stats.hit_rate ?? "—"}</span>
        <span>pollution {stats.pollution_rate ?? "—"}</span>
        <span>prefetch {stats.prefetch_used ?? 0}/{stats.prefetches ?? 0}</span>
        <span>stale blocked {stats.stale_blocked ?? 0}</span>
      </div>
      <div className="hier-grid">
        {ORDER.map((key) => {
          const lv = levels[key] || {};
          const occ = lv.occupancy == null ? null : Math.min(1, lv.occupancy);
          return (
            <div className="hier-col" key={key}>
              <h3>{lv.label || key}</h3>
              <p className="muted">{lv.description}</p>
              <div className="occ">
                <div className="occ-bar" style={{ width: `${(occ ?? 0) * 100}%` }} />
              </div>
              <div className="muted">
                {lv.used ?? 0}{lv.capacity != null ? ` / ${lv.capacity}` : ""} · {lv.count ?? 0} regions
                {lv.stale ? ` · ${lv.stale} stale` : ""}
              </div>
              <ul>
                {(lv.entries || []).slice(0, 12).map((e) => (
                  <li key={e.region_id + (e.mirror_of || "")} className={e.stale ? "stale" : ""}>
                    <b>{e.symbol}</b>
                    <span>{e.path}:{e.start_line}-{e.end_line}</span>
                    {e.why ? <em>{e.why}</em> : null}
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>
      {log.length > 0 && (
        <div className="hier-log">
          {log.slice(0, 16).map((e) => (
            <div key={e.event_id} className={"evt op-" + e.op}>
              <span>{e.turn}</span>
              <span>{e.op}</span>
              <span>{(e.symbols || []).join(", ")} {e.reason || ""}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
