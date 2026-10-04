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
from uagents_core.registration import AgentRegistrationPolicy

from ledger.agentverse import AGENT_NAME, PROTOCOL_NAME, PROTOCOL_VERSION
from ledger.agentverse.asi_one import AGENTVERSE_ADDRESS_PLACEHOLDER, ASI_ONE_CHAT_AVAILABLE, build_asi_one_protocol
from ledger.agentverse.identity import DEMO_AGENT_SEED, LOCAL_IDENTITY_LABEL, resolve_agent_mode
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


class LocalDemoRegistration(AgentRegistrationPolicy):
    """Skip Almanac registration. The process still serves ``/submit`` on localhost."""

    async def register(self, agent_identifier, identity, protocols, endpoints, metadata=None):
        return None


def build_agent(
    repo: Path | str | None = None,
    *,
    local: bool = True,
    port: int = 8000,
    seed: str | None = None,
    runtime=None,
    mailbox_api_key: str | None = None,
) -> Agent:
    service = LedgerService(repo=repo, runtime=runtime)
    service.ensure_index()
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ledger-svc")
    agent = Agent(
        name=AGENT_NAME,
        seed=seed or DEMO_AGENT_SEED,
        port=port,
        mailbox=not local,
        endpoint=f"http://127.0.0.1:{port}/submit" if local else None,
        version=PROTOCOL_VERSION,
        registration_policy=LocalDemoRegistration() if local else None,
        report_events=not local,
        description=(
            "Budgeted, explainable repository context for coding agents. "
            "Ask for a context bundle, a search, a pytest trace, or metrics."
        ),
    )
    agent.include(build_protocol(service, pool), publish_manifest=local)
    asi = build_asi_one_protocol(service, pool)
    if asi is not None:
        agent.include(asi, publish_manifest=local)

    @agent.on_event("startup")
    async def _startup(ctx: Context):
        ctx.logger.info(
            "LEDGER Runtime agent online repo=%s address=%s local=%s asi_one=%s",
            service.repo_name,
            agent.address,
            local,
            ASI_ONE_CHAT_AVAILABLE,
        )
        if local:
            ctx.logger.info("%s: %s", LOCAL_IDENTITY_LABEL, agent.address)
        else:
            ctx.logger.info("Agentverse address placeholder until registration succeeds: %s", AGENTVERSE_ADDRESS_PLACEHOLDER)

    if mailbox_api_key and not local:

        @agent.on_event("startup")
        async def _mailbox_connect(ctx: Context):
            import aiohttp

            url = f"http://127.0.0.1:{port}/connect"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        url,
                        json={"user_token": mailbox_api_key, "agent_type": "mailbox"},
                        timeout=aiohttp.ClientTimeout(total=30),
                    ) as resp:
                        body = await resp.text()
                        ctx.logger.info("mailbox connect HTTP %s: %s", resp.status, body[:400])
                        if resp.status != 200:
                            ctx.logger.error(
                                "Mailbox registration did not succeed. ASI:One submission was not completed."
                            )
            except Exception as exc:
                ctx.logger.error("Mailbox registration failed: %s. ASI:One submission was not completed.", exc)

    return agent


def run_agent(
    repo: Path | str | None = None,
    *,
    local: bool = True,
    port: int = 8000,
    seed: str | None = None,
) -> None:
    local_mode, mailbox_api_key, env_seed = resolve_agent_mode(mailbox_requested=not local)
    if not local and local_mode:
        print(
            "Missing AGENTVERSE_API_KEY or AGENT_SEED; falling back to local mode. "
            "No mailbox was registered. ASI:One submission was not submitted.",
            flush=True,
        )
    local = local_mode
    if local:
        seed = seed or DEMO_AGENT_SEED
        mailbox_api_key = None
    else:
        seed = seed or env_seed
    agent = build_agent(repo, local=local, port=port, seed=seed, mailbox_api_key=mailbox_api_key)
    if local:
        print(f"{LOCAL_IDENTITY_LABEL}: {agent.address}", flush=True)
        print(f"endpoint: http://127.0.0.1:{port}/submit", flush=True)
        label = "public demo constant, not an API key" if seed == DEMO_AGENT_SEED else "seed override"
        print(f"seed: {seed} ({label})", flush=True)
    else:
        print(
            "Mailbox mode: registering with AGENTVERSE_API_KEY. "
            "This does not mean the ASI:One submission form was submitted.",
            flush=True,
        )
        print(f"identity from AGENT_SEED: {agent.address}", flush=True)
    agent.run()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m ledger.agentverse",
        description="LEDGER Runtime uAgent (Agentverse / ASI:One)",
        epilog=(
            "ASI:One chats via official AgentChatProtocol when "
            "uagents_core.contrib.protocols.chat is installed; otherwise "
            "LedgerContextProtocol ChatText is the fallback. "
            f"Published address placeholder: {AGENTVERSE_ADDRESS_PLACEHOLDER} "
            "(replace after `ledger agentverse --mailbox`)."
        ),
    )
    p.add_argument("--repo", type=Path, default=None, help="Repository root (defaults to discover/.ledger)")
    p.add_argument("--local", action="store_true", default=True, help="Bind localhost (default)")
    p.add_argument("--mailbox", action="store_true", help="Register an Agentverse mailbox instead of --local")
    p.add_argument("--port", type=int, default=8000, help="Local HTTP port when --local")
    p.add_argument("--seed", default=None, help="Deterministic agent seed")
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    # Missing mailbox credentials fall back inside run_agent. They do not crash.
    run_agent(repo=args.repo, local=not args.mailbox, port=args.port, seed=args.seed)
