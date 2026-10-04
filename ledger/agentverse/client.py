"""Send one ContextRequest or ChatText to a running ledger-runtime agent.

Local demo (no Agentverse account)::

    python -m ledger.agentverse.client --local --port 8000 \\
        "find the code for renewal invoices ignoring loyalty discounts"

``--chat`` sends the same sentence as a chat utterance. The reply must
include ``for_renewal`` and ``shop/billing/discount_policy.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from uagents.communication import send_sync_message
from uagents.resolver import Resolver

from ledger.agentverse.identity import DEMO_AGENT_SEED, LOCAL_IDENTITY_LABEL, demo_address
from ledger.agentverse.models import ChatText, ContextBundle, ContextRequest, ErrorResponse


class StaticEndpointResolver(Resolver):
    def __init__(self, address: str, endpoint: str) -> None:
        self._address = address
        ep = endpoint.rstrip("/")
        if not ep.endswith("/submit"):
            ep += "/submit"
        self._endpoint = ep

    async def resolve(self, destination: str) -> tuple[str | None, list[str]]:
        if destination == self._address:
            return self._address, [self._endpoint]
        return None, []


async def ask(
    address: str,
    objective: str,
    endpoint: str,
    *,
    chat: bool = False,
    seed: str | None = None,
    timeout: int = 60,
):
    message = ChatText(text=objective) if chat else ContextRequest(objective=objective, seed=seed)
    response_type = ChatText if chat else ContextBundle
    return await send_sync_message(
        destination=address,
        message=message,
        response_type=response_type,
        resolver=StaticEndpointResolver(address, endpoint),
        timeout=timeout,
    )


def format_reply(result) -> str:
    if isinstance(result, ContextBundle):
        body = result.rendered_text or ""
        payload = result.dict() if hasattr(result, "dict") else result.model_dump()
        return body + "\n\n" + json.dumps(payload, indent=2)
    if isinstance(result, ChatText):
        return result.text
    if isinstance(result, ErrorResponse):
        return f"error: {result.error} {result.detail}".strip()
    return str(result)


def reply_ok(text: str) -> bool:
    return "for_renewal" in text and "shop/billing/discount_policy.py" in text


def local_service_text(
    objective: str,
    *,
    chat: bool = False,
    seed: str | None = None,
    repo=None,
) -> str:
    """In-process bundle. Used when no mailbox key is set and the agent is down."""
    from pathlib import Path

    from ledger.agentverse.service import LedgerService
    from ledger.config import LedgerConfig

    if repo is None:
        cwd = Path.cwd()
        repo = cwd / "demo_repo" if (cwd / "demo_repo" / "shop").is_dir() else cwd
    cfg = LedgerConfig.from_root(Path(repo))
    svc = LedgerService(runtime=None, repo=cfg.repo_root)
    svc.ensure_index()
    if chat:
        text, _payload = svc.handle_chat_text(objective, requester="local-fallback")
        return text
    bundle = svc.context(ContextRequest(objective=objective, seed=seed), requester="local-fallback")
    return format_reply(bundle)


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Send a context or chat request to a running ledger-runtime agent")
    p.add_argument("address", nargs="?", help="Target agent address (optional with --local)")
    p.add_argument("objective", nargs="?", help="What you are trying to find or fix")
    p.add_argument("--local", action="store_true", help=f"Use the committed demo seed ({DEMO_AGENT_SEED})")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--endpoint", default=None, help="Override the /submit URL")
    p.add_argument("--chat", action="store_true", help="Send ChatText instead of ContextRequest")
    p.add_argument("--seed", default=None, help="Optional symbol/test seed on a ContextRequest")
    p.add_argument("--timeout", type=int, default=60)
    args = p.parse_args(argv)

    objective = args.objective
    address = args.address
    if args.local:
        address = demo_address()
        if objective is None and args.address and not str(args.address).startswith("agent"):
            objective = args.address
        elif objective is None:
            objective = args.address
    if not address or not objective:
        p.error("need an objective, and an address or --local")
    endpoint = args.endpoint or f"http://127.0.0.1:{args.port}/submit"
    print(f"{LOCAL_IDENTITY_LABEL}: {address}", flush=True)
    try:
        result = asyncio.run(
            ask(address, objective, endpoint, chat=args.chat, seed=args.seed, timeout=args.timeout)
        )
        text = format_reply(result)
    except Exception as exc:
        if os.environ.get("AGENTVERSE_API_KEY", "").strip():
            sys.stderr.write(f"agentverse request failed: {exc}\n")
            raise SystemExit(1) from exc
        sys.stderr.write(
            f"Agentverse request failed ({exc}); falling back to local mode. Not a mailbox.\n"
        )
        text = local_service_text(objective, chat=args.chat, seed=args.seed)
    sys.stdout.write(text)
    if not text.endswith("\n"):
        sys.stdout.write("\n")
    if not reply_ok(text):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
