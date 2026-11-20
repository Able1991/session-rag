"""Usage and index statistics (``session-rag stats``): numbers for reports and the README."""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter, defaultdict

from . import config, usage
from .db import connect, db_path

_FALLBACK = re.compile(r"(?:\bgrep\b|\brg\b|\bfind\b|\bjq\b|\bcat\b).*(?:\.claude/projects|\.jsonl\b)")


def _scan_transcripts(since: str) -> dict:
    """Count history-tool calls vs. manual transcript greps in sessions after ``since``."""
    per = defaultdict(Counter)
    for p in config.projects().values():
        for f in p.transcript_files():
            if "/subagents/" in str(f):
                continue
            used = False
            seen = False
            try:
                fh = open(f, errors="replace")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if '"tool_use"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if (d.get("timestamp") or "") < since:
                        continue
                    seen = True
                    for it in (d.get("message") or {}).get("content") or []:
                        if not isinstance(it, dict) or it.get("type") != "tool_use":
                            continue
                        name = it.get("name", "")
                        if name.endswith("search_history"):
                            per[p.name]["search_history"] += 1
                            used = True
                        elif name.endswith("get_context") and "history" in name:
                            per[p.name]["get_context"] += 1
                        elif name.endswith("__knowledge") and "history" in name:
                            per[p.name]["knowledge"] += 1
                            used = True
                        elif name == "Bash" and _FALLBACK.search(str((it.get("input") or {}).get("command", ""))):
                            per[p.name]["manual_grep"] += 1
            if seen:
                per[p.name]["sessions"] += 1
                per[p.name]["sessions_using_history"] += int(used)
    return per


def report(since: str | None = None) -> str:
    calls = usage.read()
    since = since or (calls[0]["ts"][:10] if calls else "1970-01-01")
    lines = [f"# session-rag stats (since {since})", ""]

    con = connect(readonly=True)
    rows = con.execute("SELECT project, count(*) FROM chunks GROUP BY project ORDER BY 2 DESC").fetchall()
    con.close()
    total = sum(n for _, n in rows)
    size_mb = db_path().stat().st_size / 1e6 if db_path().exists() else 0
    lines += ["## Index", "", f"{total:,} chunks in {len(rows)} projects, {size_mb:,.0f} MB on disk", ""]
    lines += ["| project | chunks |", "|---|---:|"] + [f"| {p} | {n:,} |" for p, n in rows] + [""]

    calls = [c for c in calls if c["ts"][:10] >= since]
    if calls:
        by_tool = Counter(c["tool"] for c in calls)
        days = len({c["ts"][:10] for c in calls}) or 1
        lines += ["## MCP calls", "", f"active days: {days}, calls per active day: {len(calls) / days:.1f}", ""]
        lines += ["| tool | calls | p50 latency | p95 latency | avg response |", "|---|---:|---:|---:|---:|"]
        for tool in ("search_history", "get_context", "knowledge"):
            mine = [c for c in calls if c["tool"] == tool]
            if not mine:
                lines.append(f"| {tool} | 0 | | | |")
                continue
            lat = [c["latency_ms"] for c in mine]
            p95 = statistics.quantiles(lat, n=20)[18] if len(lat) > 1 else lat[0]
            chars = statistics.mean(c["chars"] for c in mine)
            lines.append(
                f"| {tool} | {by_tool[tool]} | {statistics.median(lat):.0f} ms | {p95:.0f} ms | "
                f"{chars:,.0f} chars (~{chars / 3.5:,.0f} tokens) |"
            )
        lines += [""]

    per = _scan_transcripts(since)
    if per:
        lines += ["## Adoption in sessions", ""]
        lines += [
            "| project | sessions | used history | search calls | knowledge calls | manual transcript greps |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for name, c in sorted(per.items(), key=lambda x: -x[1]["sessions"]):
            share = c["sessions_using_history"] / c["sessions"] * 100 if c["sessions"] else 0
            lines.append(
                f"| {name} | {c['sessions']} | {c['sessions_using_history']} ({share:.0f}%) | "
                f"{c['search_history']} | {c['knowledge']} | {c['manual_grep']} |"
            )
        lines += [""]
    return "\n".join(lines)
