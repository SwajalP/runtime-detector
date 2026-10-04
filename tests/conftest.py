from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from ledger.config import LedgerConfig
from ledger.runtime import LedgerRuntime

DEMO = Path(__file__).resolve().parents[1] / "demo_repo"


@pytest.fixture()
def demo_rt(tmp_path: Path) -> LedgerRuntime:
    """A LedgerRuntime over a scratch copy of demo_repo (never touches the real one)."""
    root = tmp_path / "demo_repo"
    shutil.copytree(DEMO, root, ignore=shutil.ignore_patterns(".ledger", "__pycache__", ".pytest_cache", ".claude", ".mcp.json"))
    cfg = LedgerConfig.from_root(root)
    rt = LedgerRuntime(cfg)
    rt.init(install_claude=False)
    return rt
