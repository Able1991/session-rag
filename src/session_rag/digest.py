"""Session digests (opt-in): Claude summarizes each day of each session into facts, causes and decisions.

Digests are written by ``claude -p`` (your Claude Code login, no API key) from the already-redacted
chunks, and stored as chunks with role ``digest`` next to the raw history. Unlike the rest of
session-rag this sends text to Anthropic, the same place the sessions themselves went.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time

from .db import connect
from .index import embed_pending
from .redact import redact
from .textproc import stem_text

SEGMENT_CHARS = 150_000  # input per call
RESULT_CHARS = 300
MODEL = os.environ.get("SESSION_RAG_DIGEST_MODEL", "haiku")

PROMPT = """Below is one day of a Claude Code working session (user, assistant, commands run, truncated output).
Write a digest that lets someone later answer questions about this work without rereading it.
Language: the language the user wrote in. No preamble. Use these sections, skip empty ones:

## Summary
2-4 sentences: what was worked on and the outcome.
## Findings
Root causes, diagnoses, how things work, with the evidence in brief.
## Decisions and changes
What was decided or changed and why: files, configs, commands, commits, deploys.
## Facts
One per line, `key: value`: hosts, IPs, URLs, paths, names of services/buckets/branches/variables,
versions, IDs, amounts, dates, people's roles, accounts. Copy values exactly. Never output secrets
(they appear as ***).
## Open
Unfinished items and next steps.

Session:
"""


def _segment_text(rows) -> str:
    out = []
    for ts, role, text in rows:
        if role == "result":
            text = text[:RESULT_CHARS] + ("…" if len(text) > RESULT_CHARS else "")
        out.append(f"[{(ts or '')[11:16]} {role}] {text}")
    return "\n".join(out)


def _segments(con, project: str, since: str | None, until: str | None):
    sql = """SELECT session, substr(ts, 1, 10) AS day, min(ts), max(ts) FROM chunks
             WHERE project = ? AND session != '' AND sidechain = 0 AND role != 'digest'"""
    args: list = [project]
    if since:
        sql += " AND ts >= ?"
        args.append(since)
    if until:
        sql += " AND ts < ?"
        args.append(until)
    sql += " GROUP BY session, day ORDER BY day, session"
    return con.execute(sql, args).fetchall()


def _call_claude(text: str, prompt: str = PROMPT) -> str:
    exe = shutil.which("claude")
    if not exe:
        raise SystemExit("claude CLI not found in PATH")
    cmd = [
        exe,
        "-p",
        "--model",
        MODEL,
        "--no-session-persistence",
        "--output-format",
        "text",
        "--tools",
        "",
        # No MCP servers: each one (e.g. a plugin's `npx firebase mcp`, ~500 MB) outlives `claude -p`
        # and leaks; hundreds of digest calls once exhausted RAM and panicked the Mac.
        "--strict-mcp-config",
        "--mcp-config",
        '{"mcpServers":{}}',
    ]
    # argv list, no shell: the model name from the environment is one argument and cannot inject a command.
    proc = subprocess.run(
        cmd,  # nosemgrep: python.lang.security.audit.dangerous-subprocess-use-tainted-env-args.dangerous-subprocess-use-tainted-env-args
        input=prompt + text,
        capture_output=True,
        text=True,
        timeout=600,
        cwd=os.path.expanduser("~"),
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[:500] or f"claude exited {proc.returncode}")
    return proc.stdout.strip()


def run(project: str, since: str | None = None, until: str | None = None, today: bool = False, log=print) -> int:
    con = connect()
    con.execute(
        """CREATE TABLE IF NOT EXISTS digests (
             source TEXT PRIMARY KEY, project TEXT, session TEXT, day TEXT, part INTEGER,
             input_chars INTEGER, output_chars INTEGER, seconds REAL, created TEXT)"""
    )
    done = {r[0] for r in con.execute("SELECT source FROM digests WHERE project = ?", (project,))}
    current_day = time.strftime("%Y-%m-%d", time.gmtime())
    made = 0
    for session, day, _, _ in _segments(con, project, since, until):
        if day == current_day and not today:
            continue  # day not finished yet
        rows = con.execute(
            """SELECT ts, role, text FROM chunks WHERE project = ? AND session = ? AND substr(ts, 1, 10) = ?
               AND sidechain = 0 AND role != 'digest' ORDER BY id""",
            (project, session, day),
        ).fetchall()
        text = _segment_text(rows)
        parts = [text[i : i + SEGMENT_CHARS] for i in range(0, len(text), SEGMENT_CHARS)] or [""]
        for n, part in enumerate(parts):
            source = f"digest:{project}:{session}:{day}:{n}"
            if source in done or len(part) < 500:
                continue
            t = time.time()
            try:
                out = redact(_call_claude(part))
            except Exception as e:  # keep going; the segment is retried next run
                log(f"{source}: failed: {e}")
                continue
            secs = time.time() - t
            ts = rows[-1][0] if rows else f"{day}T23:59:59Z"
            header = f"Digest {day} (part {n + 1}/{len(parts)}), session {session[:8]}\n"
            cur = con.execute(
                "INSERT INTO chunks(project, source, session, uuid, ts, role, sidechain, text) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (project, source, session, "", ts, "digest", 0, header + out),
            )
            con.execute(
                "INSERT INTO chunks_fts(rowid, stem, project) VALUES (?, ?, ?)",
                (cur.lastrowid, stem_text(header + out), project),
            )
            con.execute(
                "INSERT INTO digests VALUES (?,?,?,?,?,?,?,?,?)",
                (source, project, session, day, n, len(part), len(out), round(secs, 1), time.strftime("%FT%T")),
            )
            con.commit()
            made += 1
            log(f"{source}: {len(part):,} → {len(out):,} chars, {secs:.0f} s")
    embed_pending(con)
    con.close()
    return made
