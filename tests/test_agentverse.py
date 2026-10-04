import subprocess
import sys

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
    assert out.token_count <= out.budget
    assert out.bundle_id.startswith("bnd") or out.bundle_id


def test_agentverse_module_help():
    proc = subprocess.run(
        [sys.executable, "-m", "ledger.agentverse", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    assert "--repo" in proc.stdout
