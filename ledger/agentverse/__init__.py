"""LEDGER Runtime as a Fetch.ai uAgent (Agentverse / ASI:One integration).

Public pieces:

* :mod:`ledger.agentverse.models`  – structured request/response ``Model``s
  (the ``LedgerContextProtocol``).
* :mod:`ledger.agentverse.service` – framework-free glue between the models
  and :class:`ledger.runtime.LedgerRuntime` (intent parsing, rendering).
* :mod:`ledger.agentverse.agent`   – the ``ledger-runtime`` uAgent exposing
  the structured protocol **and** the official Agent Chat Protocol.
* :mod:`ledger.agentverse.client`  – example agent-to-agent client.

Run the agent with ``python -m ledger.agentverse --repo demo_repo --local``.
"""

from __future__ import annotations

__all__ = ["PROTOCOL_NAME", "PROTOCOL_VERSION", "AGENT_NAME"]

PROTOCOL_NAME = "LedgerContextProtocol"
PROTOCOL_VERSION = "0.1.0"
AGENT_NAME = "ledger-runtime"
