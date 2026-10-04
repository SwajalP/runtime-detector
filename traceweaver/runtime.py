"""TraceWeaverRuntime: wires config, storage, collector, index, and controller together."""

from __future__ import annotations

import json
from pathlib import Path

from traceweaver.config import TraceWeaverConfig
from traceweaver.db import init_db
from traceweaver.events.collector import EventCollector
from traceweaver.events.schema import new_id, now_ms
from traceweaver.gitutil import git_commit, git_repo_id
from traceweaver.index.incremental import index_repository, sync_repository
from traceweaver.policy.controller import ContextController


class TraceWeaverRuntime:
    def __init__(self, cfg: TraceWeaverConfig | None = None) -> None:
        self.cfg = cfg or TraceWeaverConfig.discover()
        self.cfg.ensure_dirs()
        self.conn = init_db(self.cfg.db_path)
        self.collector = EventCollector(self.conn)
        self.controller = ContextController(self.conn, self.cfg, self.collector)
        self.session_id: str | None = None
        sid_file = self.cfg.traceweaver_dir / "current_session"
        if sid_file.exists():
            self.session_id = sid_file.read_text().strip() or None

    # ------------------------------------------------------------------ setup

    def init(self, install_claude: bool = True) -> dict:
        from traceweaver.adapters.claude_hooks import install_claude_integration

        self.cfg.ensure_dirs()
        init_db(self.cfg.db_path)
        stats = index_repository(self.conn, self.cfg)
        claude = install_claude_integration(self.cfg) if install_claude else {}
        (self.cfg.traceweaver_dir / "config.json").write_text(json.dumps(self.cfg.to_json(), indent=2) + "\n")
        self._ensure_gitignore()
        return {"index": stats, "claude": claude, "db": str(self.cfg.db_path)}

    def reindex(self) -> dict:
        return index_repository(self.conn, self.cfg)

    def sync(self) -> dict:
        """Incremental reparse of files whose hash changed since indexing."""
        return sync_repository(self.conn, self.cfg)

    def _ensure_gitignore(self) -> None:
        gi = self.cfg.repo_root / ".gitignore"
        line = ".traceweaver/"
        try:
            text = gi.read_text() if gi.exists() else ""
            if line not in text.splitlines():
                gi.write_text((text.rstrip("\n") + "\n" if text else "") + line + "\n")
        except OSError:
            pass

    # --------------------------------------------------------------- sessions

    def ensure_session(
        self,
        agent: str = "claude",
        condition: str = "traceweaver",
        task_id: str | None = None,
        external_id: str | None = None,
    ) -> str:
        """Return the active session, creating one if needed.

        ``external_id`` (e.g. a Claude Code session id) maps 1:1 to a TraceWeaver
        session so concurrent agents do not share working sets.
        """
        if external_id:
            mapped = self.cfg.traceweaver_dir / "sessions" / f"ext-{_safe(external_id)}"
            if mapped.exists():
                sid = mapped.read_text().strip()
                if self._exists(sid):
                    self.session_id = sid
                    return sid
            sid = self._create(agent, condition, task_id, external_id)
            mapped.write_text(sid)
            return sid
        if self.session_id and self._exists(self.session_id):
            if task_id:
                self.conn.execute(
                    "UPDATE sessions SET task_id = COALESCE(task_id, ?) WHERE session_id = ?",
                    (task_id, self.session_id),
                )
                self.conn.commit()
            return self.session_id
        return self._create(agent, condition, task_id, None)

    def new_session(self, **kwargs) -> str:
        self.session_id = None
        return self.ensure_session(**kwargs)

    def _exists(self, sid: str) -> bool:
        return bool(self.conn.execute("SELECT 1 FROM sessions WHERE session_id = ?", (sid,)).fetchone())

    def _create(self, agent: str, condition: str, task_id: str | None, external_id: str | None) -> str:
        sid = new_id("sess")
        self.conn.execute(
            """
            INSERT INTO sessions(session_id, task_id, agent, condition, repo_id, commit_sha, started_ms, label)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, task_id, agent, condition, git_repo_id(self.cfg.repo_root), git_commit(self.cfg.repo_root), now_ms(), external_id),
        )
        self.conn.commit()
        self.session_id = sid
        (self.cfg.traceweaver_dir / "current_session").write_text(sid)
        self.collector.record(
            session_id=sid,
            source="controller",
            operation="prompt",
            task_id=task_id,
            extra={"agent": agent, "condition": condition, "external_id": external_id},
        )
        return sid

    def set_objective(self, session_id: str, text: str) -> None:
        self.conn.execute("UPDATE sessions SET objective = ? WHERE session_id = ?", (text[:2000], session_id))
        self.conn.commit()

    def objective(self, session_id: str) -> str | None:
        row = self.conn.execute("SELECT objective FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
        return row["objective"] if row else None

    def end_session(self, session_id: str, success: bool | None) -> None:
        self.conn.execute(
            "UPDATE sessions SET ended_ms = ?, success = ? WHERE session_id = ?",
            (now_ms(), None if success is None else (1 if success else 0), session_id),
        )
        self.conn.commit()

    def delete_session(self, session_id: str) -> None:
        """Explicit deletion for privacy: removes events, working set, bundles."""
        for table in ("events", "working_set", "co_access", "bundles", "controller_log", "sessions"):
            self.conn.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))
        self.conn.commit()

    def latest_session(self) -> str | None:
        row = self.conn.execute("SELECT session_id FROM sessions ORDER BY started_ms DESC LIMIT 1").fetchone()
        return row["session_id"] if row else self.session_id

    # -------------------------------------------------------------- dashboard

    def dashboard_state(self, session_id: str | None = None) -> dict:
        session_id = session_id or self.latest_session()
        sessions = [dict(r) for r in self.conn.execute("SELECT * FROM sessions ORDER BY started_ms DESC LIMIT 30")]
        events = self.collector.list_events(session_id, 600) if session_id else []
        ws = self.controller.working_set(session_id) if session_id else []
        metrics = self.controller.metrics(session_id) if session_id else {}
        bundle = None
        if session_id:
            row = self.conn.execute(
                "SELECT payload_json FROM bundles WHERE session_id = ? ORDER BY created_ms DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            bundle = json.loads(row["payload_json"]) if row else None
        heat: dict[str, float] = {}
        for item in ws:
            path = item.get("path") or ""
            if not path:
                continue
            heat[path] = heat.get(path, 0) + float(item.get("execution_score") or 0) + 0.15 * float(
                item.get("access_frequency") or 0
            )
        exec_path = [
            {
                "region_id": i.get("region_id"),
                "symbol": i.get("symbol"),
                "path": i.get("path"),
                "start_line": i.get("start_line"),
                "end_line": i.get("end_line"),
                "execution_score": i.get("execution_score"),
                "agent_access_score": i.get("agent_access_score"),
                "stale": i.get("stale"),
                "pinned": i.get("pinned"),
                "joined": float(i.get("execution_score") or 0) > 0.2 and float(i.get("agent_access_score") or 0) > 0.2,
            }
            for i in ws
            if float(i.get("execution_score") or 0) > 0.2
        ]
        last_eval = None
        p = self.cfg.traceweaver_dir / "last_eval.json"
        if p.exists():
            try:
                last_eval = json.loads(p.read_text())
            except json.JSONDecodeError:
                last_eval = None
        current = next((s for s in sessions if s.get("session_id") == session_id), None)
        replay = bool(
            current
            and (
                current.get("agent") == "replay"
                or str(current.get("label") or "").startswith("REPLAY")
                or (bundle or {}).get("replay")
            )
        )
        from traceweaver.lake.pipeline import lake_summary

        return {
            "session_id": session_id,
            "sessions": sessions,
            "events": events,
            "working_set": ws,
            "metrics": metrics,
            "bundle": bundle,
            "heat": heat,
            "execution_path": exec_path,
            "repo": str(self.cfg.repo_root),
            "last_eval": last_eval,
            "config": self.cfg.to_json(),
            "replay": replay,
            "session_label": (current or {}).get("label"),
            "lake": lake_summary(self.cfg),
        }


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in s)[:80]
