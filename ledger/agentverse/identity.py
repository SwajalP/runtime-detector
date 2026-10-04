"""Stable local demo identity.

The seed is a public demo constant, not an API key. The address derived from
it is a real uAgents identity for localhost. It is not an Agentverse-registered
mailbox and it is not an ASI:One submission.
"""

from __future__ import annotations

import os

DEMO_AGENT_SEED = "ledger-runtime-local-demo-v1"
LOCAL_IDENTITY_LABEL = "local demo identity, not an Agentverse-registered mailbox"


def demo_address(seed: str = DEMO_AGENT_SEED) -> str:
    from uagents_core.identity import Identity

    return Identity.from_seed(seed, 0).address


def require_mailbox_credentials() -> tuple[str, str]:
    """Return ``(AGENTVERSE_API_KEY, AGENT_SEED)`` or exit with a clear error.

    Local mode does not call this. Missing credentials must not be replaced
    with the demo seed or a made-up ``agent1q`` registration.
    """
    key = os.environ.get("AGENTVERSE_API_KEY", "").strip()
    seed = os.environ.get("AGENT_SEED", "").strip()
    missing = [name for name, value in (("AGENTVERSE_API_KEY", key), ("AGENT_SEED", seed)) if not value]
    if missing:
        raise SystemExit(
            "Cannot register an Agentverse mailbox: missing "
            + ", ".join(missing)
            + ".\n"
            "Local demo (no account):\n"
            "  python -m ledger.agentverse --repo demo_repo --local\n"
            "ASI:One Submission Agent was not submitted. No mailbox address was registered."
        )
    return key, seed
