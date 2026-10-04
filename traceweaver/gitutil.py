from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=repo, stderr=subprocess.DEVNULL, text=True, timeout=5
        )
    except Exception:
        return ""


def git_commit(repo: Path) -> str:
    out = _git(repo, "rev-parse", "HEAD").strip()
    return out[:12] if out else "uncommitted"


def git_repo_id(repo: Path) -> str:
    return repo.resolve().name


def git_dirty_files(repo: Path) -> list[str]:
    """Paths (relative to ``repo``) modified or untracked in the working tree."""
    top = _git(repo, "rev-parse", "--show-toplevel").strip()
    if not top:
        return []
    out = _git(repo, "status", "--porcelain", "--untracked-files=all", "--", ".")
    modified: list[str] = []
    untracked: list[str] = []
    top_path = Path(top)
    for line in out.splitlines():
        if len(line) < 4:
            continue
        code = line[:2]
        rel_to_top = line[3:].split(" -> ")[-1].strip().strip('"')
        full = top_path / rel_to_top
        try:
            rel = str(full.resolve().relative_to(repo.resolve()))
        except ValueError:
            continue
        if not rel.endswith(".py") or ".traceweaver" in rel:
            continue
        (untracked if code == "??" else modified).append(rel)
    # A brand-new repo has *everything* untracked, which carries no signal.
    if len(untracked) > 5:
        untracked = []
    return modified + untracked
