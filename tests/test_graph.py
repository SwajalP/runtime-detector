from fastapi.testclient import TestClient

from traceweaver.api.app import DASHBOARD_HTML, create_app
from traceweaver.api.graph import graph_payload, hierarchy_payload


def test_graph_and_hierarchy_endpoints(demo_rt):
    sid = demo_rt.new_session(agent="test", condition="traceweaver", task_id="renewal-discount")
    demo_rt.set_objective(sid, "find renewal discount policy")
    bundle = demo_rt.controller.build_bundle(sid, objective="loyalty discount on renewal", seed="for_renewal")
    assert bundle["entries"]

    graph = graph_payload(demo_rt, sid)
    assert graph["nodes"]
    assert graph["session_id"] == sid
    assert "counts" in graph

    hier = hierarchy_payload(demo_rt, sid)
    assert set(hier["order"]) == {"L0", "L1", "L2", "backing"}
    assert hier["levels"]["backing"]["count"] > 10
    assert hier["token_budget"] == demo_rt.cfg.token_budget

    app = create_app(demo_rt)
    client = TestClient(app)
    assert client.get("/api/health").json()["ok"] is True
    g = client.get("/api/graph", params={"session_id": sid}).json()
    assert g["counts"]["nodes"] >= 1
    h = client.get("/api/graph/hierarchy", params={"session_id": sid}).json()
    assert "L1" in h["levels"]
    html = client.get("/").text
    assert "TraceWeaver" in html
    # Serve prefers dashboard/dist/index.html when a Vite build exists;
    # otherwise the self-contained dashboard.html fallback.
    if "/assets/" in html:
        assert 'id="root"' in html
    else:
        assert "/api/graph" in html
        assert "Memory hierarchy" in html
        assert "Code knowledge graph" in html
    fallback = (DASHBOARD_HTML).read_text(encoding="utf-8")
    assert "Code knowledge graph" in fallback
    assert "Memory hierarchy" in fallback
    assert "replay-banner" in fallback
    assert "Structural audit" in fallback
    assert "Tarjan SCC" in fallback
    assert "Stoer–Wagner partition" in fallback
    assert "Seed side" in fallback
    assert "Reverse BFS depths" in fallback
    assert 'id="lake"' in fallback
    assert "medallion" in fallback
    lake = client.get("/api/lake").json()
    assert lake["backend"] == "local-lake"
    assert lake["databricks_called"] is False
    assert "bronze" in lake and "silver" in lake and "gold" in lake
    state = client.get("/api/state").json()
    assert state["lake"]["backend"] == "local-lake"
    audit = client.get("/api/audit").json()
    assert "large_sccs" in audit
    assert audit.get("fixture") is True or audit.get("kind") == "structural"
