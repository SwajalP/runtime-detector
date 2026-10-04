"""ledger-runtime uAgent: structured LedgerContextProtocol plus chat text.

Handlers run LEDGER work on a dedicated worker thread so the asyncio loop
never shares the sqlite connection. Inbound strings are treated as data only.
"""

from __future__ import annotations

import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from uagents import Agent, Context, Protocol

from ledger.agentverse import AGENT_NAME, PROTOCOL_NAME, PROTOCOL_VERSION
from ledger.agentverse.models import (
    ChatText,
    ContextBundle,
    ContextRequest,
    ErrorResponse,
    ExplainRequest,
    ExplainResponse,
    MetricsRequest,
    MetricsResponse,
    SearchRequest,
    SearchResponse,
    TraceRequest,
    TraceResponse,
)
from ledger.agentverse.service import LedgerService


def build_protocol(service: LedgerService, pool: ThreadPoolExecutor) -> Protocol:
    proto = Protocol(name=PROTOCOL_NAME, version=PROTOCOL_VERSION)
    loop_run = lambda fn, *args: asyncio.get_event_loop().run_in_executor(pool, fn, *args)

    @proto.on_message(model=ContextRequest, replies={ContextBundle, ErrorResponse})
    async def on_context(ctx: Context, sender: str, msg: ContextRequest):
        await ctx.send(sender, await loop_run(service.context, msg, sender))

    @proto.on_message(model=SearchRequest, replies={SearchResponse, ErrorResponse})
    async def on_search(ctx: Context, sender: str, msg: SearchRequest):
        await ctx.send(sender, await loop_run(service.search, msg, sender))

    @proto.on_message(model=ExplainRequest, replies={ExplainResponse, ErrorResponse})
    async def on_explain(ctx: Context, sender: str, msg: ExplainRequest):
        await ctx.send(sender, await loop_run(service.explain, msg, sender))

    @proto.on_message(model=TraceRequest, replies={TraceResponse, ErrorResponse})
    async def on_trace(ctx: Context, sender: str, msg: TraceRequest):
        await ctx.send(sender, await loop_run(service.trace, msg, sender))

    @proto.on_message(model=MetricsRequest, replies={MetricsResponse, ErrorResponse})
    async def on_metrics(ctx: Context, sender: str, msg: MetricsRequest):
        await ctx.send(sender, await loop_run(service.metrics, msg, sender))

    @proto.on_message(model=ChatText, replies={ChatText})
    async def on_chat(ctx: Context, sender: str, msg: ChatText):
        text, _payload = await loop_run(service.handle_chat_text, msg.text, sender)
        await ctx.send(sender, ChatText(text=text))

    return proto


def build_agent(
    repo: Path | str | None = None,
    *,
    local: bool = True,
    port: int = 8000,
    seed: str | None = None,
    runtime=None,
) -> Agent:
    service = LedgerService(repo=repo, runtime=runtime)
    service.ensure_index()
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ledger-svc")
    agent = Agent(
        name=AGENT_NAME,
        seed=seed or "ledger-runtime-local-seed",
        port=port,
        mailbox=not local,
        endpoint=f"http://127.0.0.1:{port}/submit" if local else None,
        version=PROTOCOL_VERSION,
        description=(
            "Budgeted, explainable repository context for coding agents. "
            "Ask for a context bundle, a search, a pytest trace, or metrics."
        ),
    )
    agent.include(build_protocol(service, pool), publish_manifest=local)

    @agent.on_event("startup")
    async def _startup(ctx: Context):
        ctx.logger.info(
            "LEDGER Runtime agent online repo=%s address=%s local=%s",
            service.repo_name,
            agent.address,
            local,
        )

    return agent


def run_agent(
    repo: Path | str | None = None,
    *,
    local: bool = True,
    port: int = 8000,
    seed: str | None = None,
) -> None:
    agent = build_agent(repo, local=local, port=port, seed=seed)
    agent.run()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ledger.agentverse",
        description="LEDGER Runtime uAgent (Agentverse / ASI:One)",
    )
    p.add_argument("--repo", type=Path, default=None, help="Repository root (defaults to discover/.ledger)")
    p.add_argument("--local", action="store_true", default=True, help="Bind localhost (default)")
    p.add_argument("--mailbox", action="store_true", help="Register an Agentverse mailbox instead of --local")
    p.add_argument("--port", type=int, default=8000, help="Local HTTP port when --local")
    p.add_argument("--seed", default=None, help="Deterministic agent seed")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    run_agent(repo=args.repo, local=not args.mailbox, port=args.port, seed=args.seed)
