from __future__ import annotations

import fnmatch
from pathlib import Path

from traceweaver.config import TraceWeaverConfig

REDACTED = "[redacted]"


def should_redact_path(path: str, cfg: TraceWeaverConfig) -> bool:
    name = Path(path).name
    for pattern in cfg.secret_globs:
        if fnmatch.fnmatch(name, pattern) or fnmatch.fnmatch(path, pattern):
            return True
    lowered = path.lower()
    return any(token in lowered for token in ("secret", "credential", ".env"))


def redact_text(text: str) -> str:
    lines = []
    for line in text.splitlines():
        low = line.lower()
        if any(k in low for k in ("api_key", "apikey", "password=", "secret=", "token=")):
            lines.append(REDACTED)
        else:
            lines.append(line)
    return "\n".join(lines)


def redact_env(mapping: dict[str, str]) -> dict[str, str]:
    return {k: REDACTED for k in mapping}
