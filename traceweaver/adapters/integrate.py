"""Write real MCP / tool configs for Claude Code, Cline, and Codex (GPT)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from traceweaver.adapters.claude_hooks import install_claude_integration
from traceweaver.config import TraceWeaverConfig

TARGETS = ("claude", "cline", "gpt")


def _mcp_server(cfg: TraceWeaverConfig) -> dict:
    return {
        "command": sys.executable,
        "args": ["-m", "traceweaver", "mcp", "--repo", str(cfg.repo_root)],
        "env": {"TRACEWEAVER_REPO": str(cfg.repo_root)},
    }


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return str(path)


def _cline(cfg: TraceWeaverConfig) -> dict[str, str]:
    server = _mcp_server(cfg)
    server["disabled"] = False
    server["autoApprove"] = ["traceweaver_search", "traceweaver_context", "traceweaver_explain"]
    payload = {"mcpServers": {"traceweaver": server}}
    mcp_path = _write(
        cfg.repo_root / ".cline" / "mcp_settings.json",
        json.dumps(payload, indent=2) + "\n",
    )
    rules = _write(
        cfg.repo_root / ".clinerules",
        (
            "# TraceWeaver\n\n"
            "Use the `traceweaver` MCP server (`.cline/mcp_settings.json`) before a second "
            "repository-wide search.\n\n"
            "- `traceweaver_context(objective, seed)` for a budgeted bundle\n"
            "- `traceweaver_search(query)` for a short signature list\n"
            "- `traceweaver_explain(bundle_id)` for why a region was admitted\n"
            "Raw Grep and Read stay available. TraceWeaver does not replace them.\n"
        ),
    )
    return {"mcp": mcp_path, "rules": rules}


def _gpt(cfg: TraceWeaverConfig) -> dict[str, str]:
    py = sys.executable
    repo = str(cfg.repo_root)
    toml = (
        "# Codex MCP server. Written by `traceweaver integrate gpt`.\n"
        "[mcp_servers.traceweaver]\n"
        f'command = "{py}"\n'
        "args = [\n"
        '  "-m",\n'
        '  "traceweaver",\n'
        '  "mcp",\n'
        '  "--repo",\n'
        f'  "{repo}",\n'
        "]\n\n"
        "[mcp_servers.traceweaver.env]\n"
        f'TRACEWEAVER_REPO = "{repo}"\n'
    )
    toml_path = _write(cfg.repo_root / ".codex" / "config.toml", toml)
    tools = {
        "mcp_server": _mcp_server(cfg),
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "traceweaver_search",
                    "description": "Search indexed source regions and return signature cards under a token budget.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string"},
                            "budget": {"type": "integer"},
                        },
                        "required": ["query"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "traceweaver_context",
                    "description": "Build a budgeted context bundle for an objective. Exact code for anchors, cards for neighbors.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "objective": {"type": "string"},
                            "seed": {"type": "string"},
                            "budget": {"type": "integer"},
                        },
                        "required": ["objective"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "traceweaver_explain",
                    "description": "Explain why a bundle admitted each region, including graph tags.",
                    "parameters": {
                        "type": "object",
                        "properties": {"bundle_id": {"type": "string"}},
                        "required": ["bundle_id"],
                    },
                },
            },
        ],
    }
    tools_path = _write(
        cfg.repo_root / ".gpt" / "tools.json",
        json.dumps(tools, indent=2) + "\n",
    )
    return {"codex": toml_path, "tools": tools_path}


def integrate(cfg: TraceWeaverConfig, target: str) -> dict:
    target = (target or "").strip().lower()
    if target not in TARGETS:
        raise ValueError(f"unknown integrate target {target!r}; known: {', '.join(TARGETS)}")
    cfg.ensure_dirs()
    if target == "claude":
        written = install_claude_integration(cfg)
    elif target == "cline":
        written = _cline(cfg)
    else:
        written = _gpt(cfg)
    return {"target": target, "repo": str(cfg.repo_root), "written": written}
