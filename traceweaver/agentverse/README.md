# TraceWeaver Runtime on Agentverse

Local coding-agent context. Another agent sends a task in chat or as a
`ContextRequest`. This agent returns a budgeted context bundle: the source
regions to read, with a reason for each. That bundle is the action.

The local demo identity below is **not** an Agentverse-registered mailbox.
The ASI:One submission form is not submitted by this repository.

## Run the demo (no API key)

From the repository root:

```bash
source .venv/bin/activate
traceweaver agentverse-demo --repo demo_repo
```

Missing `AGENTVERSE_API_KEY` prints `falling back to local mode` and continues.
The command writes `demo_repo/.traceweaver/agentverse_demo.json` with `fixture: false`.
The reply includes `for_renewal`.

Printed address (seed `traceweaver-runtime-local-demo-v1`, a public demo constant):

```
agent1qfdq0jc7atjkx2vgc7fdexfc29ukr4ncacw33t0csuxydt23wuy0q7npan4
```

## Two processes

```bash
source .venv/bin/activate
python -m traceweaver.agentverse --repo demo_repo --local --port 8000
python -m traceweaver.agentverse.client --local --port 8000 --chat \
  "find the code for renewal invoices ignoring loyalty discounts"
```

`traceweaver agentverse --mailbox` with either `AGENTVERSE_API_KEY` or `AGENT_SEED`
unset falls back to local mode and does not crash.

## Protocols

- `TraceWeaverContextProtocol` — `ContextRequest`, `SearchRequest`, `ExplainRequest`, `TraceRequest`, `MetricsRequest`
- ASI:One `AgentChatProtocol` when `uagents_core.contrib.protocols.chat` is installed
- `ChatText` keyword routing for the same intents when that package is absent

Inbound text is data. It is not passed to a shell or `eval`.

## What a human still has to do

1. Create an Agentverse API key in the Agentverse UI. Do not commit it.
2. Choose a private `AGENT_SEED`. Do not commit it.
3. `export AGENTVERSE_API_KEY=...` and `export AGENT_SEED=...`
4. `python -m traceweaver.agentverse --repo demo_repo --mailbox`
5. Confirm the log shows a successful mailbox connect. That address comes from `AGENT_SEED`. Do not submit the local demo address as a registered mailbox.
6. Submit the hackathon ASI:One Submission Agent form yourself. This repository does not submit that form.

See `docs/AGENTVERSE.md`.
