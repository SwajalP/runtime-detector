"""One-shot local uAgent demo for the renewal-discount chat.

Prefers a real localhost round-trip. If that socket path fails, the same
handler runs in-process. A missing ``AGENTVERSE_API_KEY`` stays on localhost
and is recorded as such. This command does not register a mailbox and does
not submit the ASI:One form.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from traceweaver.agentverse.identity import DEMO_AGENT_SEED, LOCAL_IDENTITY_LABEL, demo_address
from traceweaver.config import TraceWeaverConfig

RENEWAL_CHAT = "find the code for renewal invoices ignoring loyalty discounts"
_JSON_FENCE = re.compile(r"```json\s*(\{.*\})\s*```", re.DOTALL)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _bundle_from_text(text: str) -> dict | None:
    match = _JSON_FENCE.search(text or "")
    if not match:
        return None
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _includes_for_renewal(text: str, bundle: dict | None) -> bool:
    if "for_renewal" in (text or ""):
        return True
    blob = json.dumps(bundle or {})
    return "for_renewal" in blob


def _missing_key_note() -> str | None:
    if os.environ.get("AGENTVERSE_API_KEY", "").strip():
        return None
    return (
        "AGENTVERSE_API_KEY is unset; falling back to local mode. "
        "No mailbox was registered. The ASI:One submission form was not submitted."
    )


def _inprocess(cfg: TraceWeaverConfig, requester: str) -> tuple[str, dict]:
    from traceweaver.agentverse.service import TraceWeaverService

    service = TraceWeaverService(repo=cfg.repo_root)
    service.ensure_index()
    return service.handle_chat_text(RENEWAL_CHAT, requester=requester)


def _roundtrip(cfg: TraceWeaverConfig, port: int, address: str) -> str:
    from traceweaver.agentverse.client import ask, format_reply

    project_root = Path(__file__).resolve().parents[2]
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "traceweaver.agentverse",
            "--repo",
            str(cfg.repo_root),
            "--local",
            "--port",
            str(port),
        ],
        cwd=str(project_root),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    logs: list[str] = []

    def _drain() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            logs.append(line)

    threading.Thread(target=_drain, daemon=True).start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"local agent exited {proc.returncode}: {''.join(logs)[-800:]}")
            if _port_open(port):
                break
            time.sleep(0.2)
        else:
            raise RuntimeError(f"local agent did not listen on {port}: {''.join(logs)[-800:]}")

        last_error: Exception | None = None
        for _ in range(8):
            try:
                result = asyncio.run(
                    ask(
                        address,
                        RENEWAL_CHAT,
                        f"http://127.0.0.1:{port}/submit",
                        chat=True,
                        timeout=90,
                    )
                )
                text = format_reply(result)
                if "for_renewal" in text:
                    return text
                last_error = RuntimeError("reply did not include for_renewal")
            except Exception as exc:
                last_error = exc
            time.sleep(0.5)
        raise RuntimeError(str(last_error) if last_error else "local round-trip failed")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def run_agentverse_demo(
    cfg: TraceWeaverConfig,
    *,
    port: int | None = None,
    roundtrip: bool = True,
) -> dict:
    """Send the renewal chat and write ``.traceweaver/agentverse_demo.json``."""
    cfg.ensure_dirs()
    address = demo_address()
    notes: list[str] = []
    missing = _missing_key_note()
    if missing:
        notes.append(missing)
    notes.append(
        f"{LOCAL_IDENTITY_LABEL}. Seed {DEMO_AGENT_SEED} is a public demo constant, not an API key."
    )

    transport = "local-roundtrip"
    reply = ""
    bundle: dict | None = None
    if roundtrip:
        use_port = port or _free_port()
        try:
            reply = _roundtrip(cfg, use_port, address)
            bundle = _bundle_from_text(reply)
        except Exception as exc:
            transport = "in-process"
            notes.append(
                f"Local socket round-trip failed ({exc}). Used the in-process chat handler. Not a mailbox."
            )
            reply, bundle = _inprocess(cfg, address)
    else:
        transport = "in-process"
        notes.append("In-process handler (round-trip not requested). Not a mailbox.")
        reply, bundle = _inprocess(cfg, address)

    if bundle is None:
        bundle = _bundle_from_text(reply) or {}

    report = {
        "fixture": False,
        "live_model": False,
        "mailbox_registered": False,
        "asi_one_form_submitted": False,
        "address": address,
        "address_label": LOCAL_IDENTITY_LABEL,
        "seed": DEMO_AGENT_SEED,
        "transport": transport,
        "objective": RENEWAL_CHAT,
        "includes_for_renewal": _includes_for_renewal(reply, bundle),
        "bundle": bundle,
        "reply": reply,
        "notes": notes,
        "backend": "local-uagent",
    }
    path = cfg.traceweaver_dir / "agentverse_demo.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    report["written"] = str(path)
    return report


def format_demo(report: dict) -> str:
    lines = list(report.get("notes") or [])
    lines.append(f"{report.get('address_label')}: {report.get('address')}")
    lines.append(f"transport: {report.get('transport')}")
    lines.append(f"fixture: {str(report.get('fixture')).lower()}")
    bundle = report.get("bundle") or {}
    entries = bundle.get("entries") or []
    if entries:
        lines.append(f"bundle {bundle.get('bundle_id')}:")
        for entry in entries[:12]:
            symbol = entry.get("symbol") or "(module)"
            path = entry.get("path") or ""
            lines.append(f"  {symbol}  {path}")
    else:
        text = report.get("reply") or ""
        lines.extend(text.splitlines()[:24])
    lines.append(f"includes for_renewal: {str(bool(report.get('includes_for_renewal'))).lower()}")
    lines.append(f"written: {report.get('written')}")
    return "\n".join(lines)
