from ledger.mcp.server import handle
from ledger.mcp.tools import TOOLS, render_bundle_text, render_search_text


def test_tools_schema():
    names = {t["name"] for t in TOOLS}
    assert names == {"ledger_search", "ledger_context", "ledger_explain"}


def test_mcp_initialize_and_search(demo_rt):
    init = handle(demo_rt, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init["result"]["serverInfo"]["name"] == "ledger-runtime"
    listed = handle(demo_rt, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert len(listed["result"]["tools"]) == 3
    call = handle(
        demo_rt,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "ledger_search", "arguments": {"query": "for_renewal"}},
        },
    )
    result = call["result"]["structuredContent"]
    assert result["regions"]
    assert "for_renewal" in render_search_text(result)
    bundle = handle(
        demo_rt,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "ledger_context", "arguments": {"objective": "fix renewal loyalty", "seed": "for_renewal"}},
        },
    )
    payload = bundle["result"]["structuredContent"]
    assert payload["entries"]
    text = render_bundle_text(payload)
    assert payload["bundle_id"] in text
