"""Turn Claude Code transcripts (jsonl) and markdown docs into searchable chunks."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MAX_CHUNK = 1500
OVERLAP = 200
MIN_CHUNK = 25
TOOL_RESULT_LIMIT = 1200
TOOL_INPUT_LIMIT = 1000
SKIP_TOOLS = {
    "ToolSearch", "TodoWrite", "TaskStop", "TaskOutput", "ReadNotifications", "ScheduleWakeup",
}  # fmt: skip

_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
_NOISE = re.compile(r"<(local-command-caveat|command-name|command-message|command-args)>.*?</\1>", re.S)


@dataclass
class Chunk:
    source: str
    session: str
    uuid: str
    ts: str
    role: str  # user | assistant | tool | result | doc
    text: str
    sidechain: bool = False


def _clean(text: str) -> str:
    return _NOISE.sub("", _REMINDER.sub("", text)).strip()


def split(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK:
        return [text]
    step = MAX_CHUNK - OVERLAP
    return [text[i : i + MAX_CHUNK] for i in range(0, len(text) - OVERLAP, step)]


def _tool_use_text(item: dict) -> str:
    name = item.get("name", "")
    inp = item.get("input") or {}
    if name == "Bash":
        body = f"{inp.get('description', '')}\n$ {inp.get('command', '')}"
    elif name in ("Edit", "Write", "Read"):
        body = str(inp.get("file_path", ""))
        if name == "Edit":
            body += f"\n- {str(inp.get('old_string', ''))[:300]}\n+ {str(inp.get('new_string', ''))[:500]}"
    else:
        body = json.dumps(inp, ensure_ascii=False)
    return f"[{name}] {body}"[:TOOL_INPUT_LIMIT]


def _result_text(item: dict) -> str:
    c = item.get("content")
    if isinstance(c, list):
        c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    c = str(c or "")
    if len(c) > TOOL_RESULT_LIMIT:
        c = c[: TOOL_RESULT_LIMIT - 200] + "\n…\n" + c[-200:]
    return c


def parse_transcript(path: Path, offset: int = 0) -> tuple[list[Chunk], int]:
    """Chunks from complete lines after ``offset``; returns the new offset."""
    with open(path, "rb") as f:
        f.seek(offset)
        data = f.read()
    end = data.rfind(b"\n")
    if end < 0:
        return [], offset
    chunks: list[Chunk] = []
    for raw in data[: end + 1].splitlines():
        try:
            d = json.loads(raw)
        except ValueError:
            continue
        t = d.get("type")
        if t not in ("user", "assistant"):
            continue
        content = (d.get("message") or {}).get("content")
        base = dict(
            source=str(path),
            session=d.get("sessionId", ""),
            uuid=d.get("uuid", ""),
            ts=d.get("timestamp", ""),
            sidechain=bool(d.get("isSidechain")),
        )
        items = [{"type": "text", "text": content}] if isinstance(content, str) else (content or [])
        for it in items:
            if not isinstance(it, dict):
                continue
            kind = it.get("type")
            if kind == "text":
                role, text = t, _clean(it.get("text", ""))
            elif kind == "tool_use":
                if it.get("name") in SKIP_TOOLS:
                    continue
                role, text = "tool", _tool_use_text(it)
            elif kind == "tool_result":
                role, text = "result", _result_text(it)
            else:
                continue
            if len(text) < MIN_CHUNK:
                continue
            chunks.extend(Chunk(role=role, text=part, **base) for part in split(text))
    return chunks, offset + end + 1


_HEADING = re.compile(r"^#{1,4} .+$", re.M)


def parse_markdown(path: Path) -> list[Chunk]:
    text = path.read_text(errors="replace")
    ts = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    bounds = [m.start() for m in _HEADING.finditer(text)]
    if not bounds or bounds[0] != 0:
        bounds.insert(0, 0)
    bounds.append(len(text))
    chunks = []
    for a, b in zip(bounds, bounds[1:], strict=False):
        section = text[a:b].strip()
        if len(section) < MIN_CHUNK:
            continue
        head = section.splitlines()[0][:120]
        for i, part in enumerate(split(section)):
            body = part if i == 0 else f"{head}\n…{part}"
            chunks.append(Chunk(source=str(path), session="", uuid="", ts=ts, role="doc", text=f"{path.name}: {body}"))
    return chunks
