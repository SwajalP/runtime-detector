"""Local uAgent plus a second process. No Agentverse account."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from traceweaver.agentverse.identity import demo_address

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "demo_repo"
UTTERANCE = "find the code for renewal invoices ignoring loyalty discounts"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_port(port: int, proc: subprocess.Popen, logs: list[str], timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise AssertionError(f"agent exited {proc.returncode}: {''.join(logs)}")
        with socket.socket() as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    proc.kill()
    raise AssertionError(f"agent did not listen on {port}: {''.join(logs)}")


def _ask(port: int, *extra: str) -> subprocess.CompletedProcess[str]:
    last = None
    for _ in range(8):
        last = subprocess.run(
            [sys.executable, "-m", "traceweaver.agentverse.client", "--local", "--port", str(port), *extra, UTTERANCE],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=90,
            check=False,
        )
        if last.returncode == 0 and "for_renewal" in last.stdout:
            return last
        time.sleep(0.5)
    assert last is not None
    return last


@pytest.fixture()
def agent_proc(tmp_path: Path):
    root = tmp_path / "demo_repo"
    shutil.copytree(
        DEMO,
        root,
        ignore=shutil.ignore_patterns(".traceweaver", "__pycache__", ".pytest_cache", ".claude", ".mcp.json"),
    )
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "traceweaver.agentverse", "--repo", str(root), "--local", "--port", str(port)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logs: list[str] = []

    def _drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            logs.append(line)

    threading.Thread(target=_drain, daemon=True).start()
    try:
        _wait_port(port, proc, logs)
        yield port, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def test_local_client_context_and_chat(agent_proc):
    port, proc = agent_proc
    context = _ask(port)
    assert context.returncode == 0, context.stdout + context.stderr
    assert demo_address() in context.stdout
    assert "for_renewal" in context.stdout
    assert "shop/billing/discount_policy.py" in context.stdout
    assert "local demo identity" in context.stdout

    chat = _ask(port, "--chat")
    assert chat.returncode == 0, chat.stdout + chat.stderr
    assert "for_renewal" in chat.stdout
    assert "shop/billing/discount_policy.py" in chat.stdout
    assert "```json" in chat.stdout
    assert proc.poll() is None
