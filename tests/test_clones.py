"""AST-normalized exact clones share a cost of (copies - 1) * tokens."""

from traceweaver.graphalg.clones import analyze_clones

SRC_ALPHA = (
    "def alpha(left, right):\n"
    "    total = left + right\n"
    "    if total > 0:\n"
    "        return total\n"
    "    return 0\n"
)
SRC_BETA = (
    "def beta(x, y):\n"
    "    acc = x + y\n"
    "    if acc > 0:\n"
    "        return acc\n"
    "    return 0\n"
)
SRC_GAMMA = (
    "def gamma(n):\n"
    "    try:\n"
    "        while n:\n"
    "            n = n - 1\n"
    "        raise RuntimeError('stop')\n"
    "    except RuntimeError:\n"
    "        return None\n"
)
SRC_GET_A = "def get_a(self):\n    return self.value\n"
SRC_GET_B = "def get_b(obj):\n    return obj.value\n"


def test_exact_clones_cluster_and_cost():
    report = analyze_clones(
        [
            {"name": "alpha", "path": "a.py", "source": SRC_ALPHA},
            {"name": "beta", "path": "b.py", "source": SRC_BETA},
            {"name": "gamma", "path": "c.py", "source": SRC_GAMMA},
            {"name": "get_a", "path": "d.py", "source": SRC_GET_A},
            {"name": "get_b", "path": "e.py", "source": SRC_GET_B},
        ]
    )
    assert report["unit"] == "debt-tokens"
    names = [tuple(sorted(member["name"] for member in cluster["members"])) for cluster in report["clusters"]]
    assert names == [("alpha", "beta")]
    cluster = report["clusters"][0]
    assert cluster["match"] == "exact"
    assert cluster["similarity"] == 1
    assert cluster["copies"] == 2
    tokens = max(1, len(SRC_ALPHA) // 4)
    assert cluster["estimated_tokens"] == tokens
    assert cluster["redundant_cost"] == (2 - 1) * tokens
    assert cluster["savings"] == cluster["redundant_cost"]
    assert report["total_redundant_cost"] == cluster["redundant_cost"]
    assert report["total_savings"] == cluster["savings"]
    assert "AST normalization" in cluster["note"]
    assert "gamma" not in {member["name"] for member in cluster["members"]}
