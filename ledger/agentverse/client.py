"""Example agent-to-agent client for LedgerContextProtocol.

This is a reference, not a required runtime dependency. It constructs a
short-lived uAgent that sends one ``ContextRequest`` and prints the reply.
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from uagents import Agent, Context

from ledger.agentverse.models import ContextBundle, ContextRequest, ErrorResponse


def build_client(target: str, objective: str, seed: str | None = None, port: int = 8001) -> Agent:
    client = Agent(name="ledger-client", seed="ledger-runtime-client-seed", port=port, mailbox=False)

    @client.on_event("startup")
    async def _ask(ctx: Context):
        await ctx.send(target, ContextRequest(objective=objective, seed=seed))

    @client.on_message(model=ContextBundle)
    async def _bundle(ctx: Context, sender: str, msg: ContextBundle):
        ctx.logger.info("%s", msg.rendered_text or msg.bundle_id)
        await asyncio.sleep(0.2)
        raise SystemExit(0)

    @client.on_message(model=ErrorResponse)
    async def _err(ctx: Context, sender: str, msg: ErrorResponse):
        ctx.logger.error("%s: %s", msg.error, msg.detail)
        raise SystemExit(2)

    return client


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Send a ContextRequest to a running ledger-runtime agent")
    p.add_argument("address", help="Target agent address")
    p.add_argument("objective", help="What you are trying to find or fix")
    p.add_argument("--seed", default=None)
    p.add_argument("--port", type=int, default=8001)
    args = p.parse_args(argv)
    build_client(args.address, args.objective, seed=args.seed, port=args.port).run()


if __name__ == "__main__":
    main()
