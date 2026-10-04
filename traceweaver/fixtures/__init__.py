"""Load labeled fixtures shipped with the package."""

from __future__ import annotations

import json
from pathlib import Path


def fixture_path(name: str) -> Path:
    return Path(__file__).resolve().parent / name


def load_fixture(name: str) -> dict:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))
