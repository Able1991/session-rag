"""Local usage log of MCP tool calls, used by ``session-rag stats``. Never leaves the machine."""

from __future__ import annotations

import json
import os
import time

from .config import DATA_DIR
from .redact import redact

LOG = DATA_DIR / "usage.jsonl"


def log_call(tool: str, project: str | None, latency_ms: float, results: int, chars: int, query: str = "") -> None:
    if os.environ.get("SESSION_RAG_NO_USAGE_LOG"):
        return
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": tool,
        "project": project,
        "latency_ms": round(latency_ms, 1),
        "results": results,
        "chars": chars,
        "query": redact(query)[:200],
    }
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def read() -> list[dict]:
    try:
        return [json.loads(line) for line in LOG.read_text().splitlines() if line.strip()]
    except (OSError, ValueError):
        return []
