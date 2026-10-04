import subprocess
import sys

from ledger.agentverse.asi_one import AGENTVERSE_ADDRESS_PLACEHOLDER, ASI_ONE_CHAT_AVAILABLE, build_asi_one_protocol
from ledger.agentverse.models import ContextRequest
from ledger.agentverse.service import Intent, LedgerService, parse_intent


def test_parse_intent_routes():
    assert parse_intent("help")[0] == Intent.HELP
    assert parse_intent("search for_renewal") == (Intent.SEARCH, {"query": "for_renewal"})
    assert parse_intent("metrics")[0] == Intent.METRICS
    assert parse_intent("explain bnd_abcde")[0] == Intent.EXPLAIN
    intent, params = parse_intent("find the loyalty discount on annual renewal")
    assert intent == Intent.CONTEXT
    assert "loyalty" in params["objective"]


def test_service_context_includes_discount_policy(demo_rt):
    svc = LedgerService(runtime=demo_rt)
    out = svc.context(ContextRequest(objective="renewal invoices ignore loyalty discounts", seed="for_renewal"))
    assert out.entries
    assert any("discount_policy" in e.path for e in out.entries)
    assert any((e.symbol or "") == "for_renewal" for e in out.entries)
    assert out.token_count <= out.budget
    assert out.bundle_id.startswith("bnd") or out.bundle_id


def test_chat_roundtrip_bundle_contains_for_renewal(demo_rt):
    svc = LedgerService(runtime=demo_rt)
    text, payload = svc.handle_chat_text(
        "find the loyalty discount on annual renewal `for_renewal`",
        requester="agent1qtestlocal",
    )
    symbols = [e.get("symbol") for e in payload.get("entries", [])]
    assert "for_renewal" in symbols
    assert "for_renewal" in text or "discount_policy" in text


def test_asi_one_handler_is_documented_and_wired():
    assert "agent1q" in AGENTVERSE_ADDRESS_PLACEHOLDER
    assert "LEDGER_RUNTIME_AGENTVERSE_ADDRESS" in AGENTVERSE_ADDRESS_PLACEHOLDER
    if ASI_ONE_CHAT_AVAILABLE:
        from concurrent.futures import ThreadPoolExecutor

        proto = build_asi_one_protocol(object(), ThreadPoolExecutor(max_workers=1))
        assert proto is not None
        assert proto.name == "AgentChatProtocol"
    else:
        assert build_asi_one_protocol(None, None) is None


def test_agentverse_module_help():
    proc = subprocess.run(
        [sys.executable, "-m", "ledger.agentverse", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--repo" in proc.stdout
    assert "ASI:One" in proc.stdout or "AgentChatProtocol" in proc.stdout
    assert "agent1q" in proc.stdout
