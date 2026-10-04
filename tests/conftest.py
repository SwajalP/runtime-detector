from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from traceweaver.config import TraceWeaverConfig
from traceweaver.runtime import TraceWeaverRuntime

DEMO = Path(__file__).resolve().parents[1] / "demo_repo"


@pytest.fixture()
def demo_rt(tmp_path: Path) -> TraceWeaverRuntime:
    """A TraceWeaverRuntime over a scratch copy of demo_repo (never touches the real one)."""
    root = tmp_path / "demo_repo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns(".traceweaver", "__pycache__", ".pytest_cache", ".claude", ".mcp.json"))
    cfg = TraceWeaverConfig.from_root(root)
    rt = TraceWeaverRuntime(cfg)
    rt.init(install_claude=False)
    return rt
