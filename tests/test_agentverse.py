import subprocess
import sys

import pytest

from ledger.agentverse.asi_one import AGENTVERSE_ADDRESS_PLACEHOLDER, ASI_ONE_CHAT_AVAILABLE, build_asi_one_protocol
from ledger.agentverse.identity import DEMO_AGENT_SEED, LOCAL_IDENTITY_LABEL, demo_address, require_mailbox_credentials
from ledger.agentverse.models import ContextRequest
from ledger.agentverse.service import Intent, LedgerService, parse_intent

CHAT_UTTERANCE = "find the code for renewal invoices ignoring loyalty discounts"


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


def test_demo_address_is_stable_and_labeled():
    address = demo_address()
    assert address.startswith("agent1")
    assert demo_address() == demo_address(DEMO_AGENT_SEED)
    assert "not an Agentverse-registered mailbox" in LOCAL_IDENTITY_LABEL


def test_mailbox_missing_key_errors(monkeypatch):
    monkeypatch.delenv("AGENTVERSE_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_SEED", raising=False)
    with pytest.raises(SystemExit) as exc:
        require_mailbox_credentials()
    assert "AGENTVERSE_API_KEY" in str(exc.value)
    assert "not submitted" in str(exc.value).lower() or "was not submitted" in str(exc.value)


def test_missing_mailbox_key_falls_back_to_local(monkeypatch):
    from ledger.agentverse.identity import resolve_agent_mode

    monkeypatch.delenv("AGENTVERSE_API_KEY", raising=False)
    monkeypatch.delenv("AGENT_SEED", raising=False)
    local, key, seed = resolve_agent_mode(True)
    assert local is True
    assert key is None
    assert seed is None
    monkeypatch.setenv("AGENTVERSE_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_SEED", "test-seed")
    local, key, seed = resolve_agent_mode(True)
    assert local is False
    assert key == "test-key"
    assert seed == "test-seed"


def test_local_client_fallback_returns_for_renewal(demo_rt):
    from ledger.agentverse.client import local_service_text

    text = local_service_text(
        "find the code for renewal invoices ignoring loyalty discounts",
        chat=False,
        seed="for_renewal",
        repo=demo_rt.cfg.repo_root,
    )
    assert "for_renewal" in text
    assert "shop/billing/discount_policy.py" in text


def test_chat_utterance_returns_for_renewal_and_json(demo_rt):
    svc = LedgerService(runtime=demo_rt)
    text, payload = svc.handle_chat_text(CHAT_UTTERANCE, requester="agent1qlocaldemo")
    assert "for_renewal" in text
    assert "shop/billing/discount_policy.py" in text
    assert "```json" in text
    symbols = [e.get("symbol") for e in payload.get("entries", [])]
    paths = [e.get("path") for e in payload.get("entries", [])]
    assert "for_renewal" in symbols
    assert "shop/billing/discount_policy.py" in paths


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
