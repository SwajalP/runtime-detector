"""TraceWeaver Runtime as a Fetch.ai uAgent (Agentverse / ASI:One integration).

Public pieces:

* :mod:`traceweaver.agentverse.models`  – structured request/response ``Model``s
  (the ``TraceWeaverContextProtocol``).
* :mod:`traceweaver.agentverse.service` – framework-free glue between the models
  and :class:`traceweaver.runtime.TraceWeaverRuntime` (intent parsing, rendering).
* :mod:`traceweaver.agentverse.agent`   – the ``traceweaver-runtime`` uAgent exposing
  the structured protocol, official ASI:One ``AgentChatProtocol``, and
  keyword-routed ``ChatText`` fallback.
* :mod:`traceweaver.agentverse.asi_one` – ASI:One chat handler + address placeholder.
* :mod:`traceweaver.agentverse.client`  – example agent-to-agent client.

Run the agent with ``python -m traceweaver.agentverse --repo demo_repo --local``.
Published Agentverse address placeholder: ``agent1q<TRACEWEAVER_RUNTIME_AGENTVERSE_ADDRESS>``.
"""

from __future__ import annotations

__all__ = ["PROTOCOL_NAME", "PROTOCOL_VERSION", "AGENT_NAME"]

PROTOCOL_NAME = "TraceWeaverContextProtocol"
PROTOCOL_VERSION = "0.1.0"
AGENT_NAME = "traceweaver-runtime"
