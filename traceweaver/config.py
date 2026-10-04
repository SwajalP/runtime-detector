from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDES = (
    ".git",
    ".traceweaver",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
)

SECRET_GLOBS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "credentials.json",
    "id_rsa",
    "*.p12",
    "secrets.yaml",
    "secrets.yml",
)

POLICY_WEIGHTS = {
    "lexical": 0.24,
    "execution": 0.26,
    "structural": 0.14,
    "co_access": 0.12,
    "recency": 0.08,
    "diagnostic": 0.22,
    "token_cost": -0.08,
    "staleness": -0.30,
}


@dataclass
class TraceWeaverConfig:
    repo_root: Path
    traceweaver_dir: Path
    db_path: Path
    token_budget: int = 2400
    prefetch_limit: int = 2
    admission_threshold: float = 0.28
    decay_rho: float = 0.92
    top_neighbors: int = 8
    host: str = "127.0.0.1"
    port: int = 8765
    excludes: tuple[str, ...] = DEFAULT_EXCLUDES
    secret_globs: tuple[str, ...] = SECRET_GLOBS
    language: str = "python"
    parser_backend: str = "tree-sitter"  # or "ast"
    # Standalone leftover successors score ~0.23. Blend parent heat, but only
    # unused callees of HIGH-score admitted regions (not every leftover edge).
    prefetch_threshold: float = 0.62
    # HIGH parent: above admission (0.28) and the exact-code cutoff (0.55).
    prefetch_parent_min_score: float = 0.70
    prefetch_parent_min_p: float = 0.90
    # Re-run `pytest` under coverage when the agent runs it via Bash so the
    # program trace can be joined with the agent trace. Cheap for small suites.
    trace_agent_tests: bool = True
    observe_only: bool = False  # baseline mode: hooks record, never advise
    weights: dict[str, float] = field(default_factory=lambda: dict(POLICY_WEIGHTS))

    @classmethod
    def discover(cls, start: Path | None = None) -> "TraceWeaverConfig":
        root = (start or Path.cwd()).resolve()
        for candidate in [root, *root.parents]:
            traceweaver_dir = candidate / ".traceweaver"
            if traceweaver_dir.is_dir():
                return cls.from_root(candidate)
        return cls.from_root(root)

    @classmethod
    def from_root(cls, repo_root: Path) -> "TraceWeaverConfig":
        repo_root = repo_root.resolve()
        demo = repo_root / "demo_repo"
        if (demo / "shop").is_dir() and not (repo_root / "shop").is_dir():
            repo_root = demo
        traceweaver_dir = repo_root / ".traceweaver"
        token_budget = int(os.environ.get("TRACEWEAVER_TOKEN_BUDGET", "2400"))
        port = int(os.environ.get("TRACEWEAVER_PORT", "8765"))
        cfg = cls(
            repo_root=repo_root,
            traceweaver_dir=traceweaver_dir,
            db_path=traceweaver_dir / "traceweaver.db",
            token_budget=token_budget,
            port=port,
            parser_backend=os.environ.get("TRACEWEAVER_PARSER", "tree-sitter"),
            observe_only=os.environ.get("TRACEWEAVER_OBSERVE_ONLY", "") == "1",
        )
        saved = traceweaver_dir / "config.json"
        if saved.exists():
            try:
                import json

                data = json.loads(saved.read_text())
                for key in (
                    "token_budget",
                    "prefetch_limit",
                    "admission_threshold",
                    "prefetch_threshold",
                    "prefetch_parent_min_score",
                    "prefetch_parent_min_p",
                    "trace_agent_tests",
                ):
                    if key in data and f"TRACEWEAVER_{key.upper()}" not in os.environ:
                        setattr(cfg, key, type(getattr(cfg, key))(data[key]))
                if isinstance(data.get("weights"), dict):
                    cfg.weights.update({k: float(v) for k, v in data["weights"].items()})
            except Exception:
                pass
        return cfg

    def to_json(self) -> dict:
        return {
            "repo": str(self.repo_root),
            "token_budget": self.token_budget,
            "prefetch_limit": self.prefetch_limit,
            "prefetch_threshold": self.prefetch_threshold,
            "prefetch_parent_min_score": self.prefetch_parent_min_score,
            "prefetch_parent_min_p": self.prefetch_parent_min_p,
            "admission_threshold": self.admission_threshold,
            "trace_agent_tests": self.trace_agent_tests,
            "port": self.port,
            "parser_backend": self.parser_backend,
            "weights": self.weights,
            "captured_event_types": [
                "prompt", "glob", "grep", "read", "edit", "execute", "test",
                "admit", "hit", "miss", "evict", "invalidate", "prefetch", "bundle_served", "complete",
            ],
            "excluded_paths": list(self.excludes),
            "redacted_globs": list(self.secret_globs),
        }

    def ensure_dirs(self) -> None:
        self.traceweaver_dir.mkdir(parents=True, exist_ok=True)
        (self.traceweaver_dir / "hooks").mkdir(exist_ok=True)
        (self.traceweaver_dir / "sessions").mkdir(exist_ok=True)

    def is_excluded(self, path: Path) -> bool:
        parts = set(path.parts)
        if parts & set(self.excludes):
            return True
        name = path.name
        if name.endswith(".pyc"):
            return True
        return False
