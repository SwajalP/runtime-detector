"""Local vector index: token overlap ranks first. No network."""

from traceweaver.config import TraceWeaverConfig
from traceweaver.db import init_db
from traceweaver.index.embeddings import VectorIndex


def test_token_overlap_region_ranks_first(tmp_path):
    cfg = TraceWeaverConfig.from_root(tmp_path)
    cfg.ensure_dirs()
    conn = init_db(cfg.db_path)
    index = VectorIndex(cfg, conn)
    index.upsert(
        [
            {
                "region_id": "overlap",
                "symbol": "apply_loyalty_discount",
                "kind": "function",
                "signature": "def apply_loyalty_discount(invoice):",
                "body": '    """apply loyalty discount on the invoice"""\n    return invoice\n',
            },
            {
                "region_id": "noise",
                "symbol": "render_catalog_tile",
                "kind": "function",
                "signature": "def render_catalog_tile(product):",
                "body": '    """render a catalog tile"""\n    return product\n',
            },
            {
                "region_id": "other",
                "symbol": "schedule_webhook_retry",
                "kind": "function",
                "signature": "def schedule_webhook_retry(event):",
                "body": '    """schedule a webhook retry"""\n    return event\n',
            },
        ]
    )
    hits = index.query("apply loyalty discount invoice", k=3)
    assert hits[0]["region_id"] == "overlap"
    status = index.status()
    assert status["backend"] == "sqlite-cosine"
    assert status["model"] == "local-hash"
    assert status["count"] == 3
    assert status["dimension"] == 256
    conn.close()
