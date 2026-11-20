"""Retrieval evaluation (``session-rag eval cases.toml``).

Cases file::

    [[case]]
    query = "how did we fix the load balancer health checks"
    project = "infra"                 # optional; omit to search all projects
    # top-level `until = "2026-11-01T09:00:00Z"` ignores chunks written after that (the eval-writing session)
    expect = ["node-selector"]        # a hit is any chunk containing one of these (case-insensitive)
    variants = ["octavia pool health check", "LoadBalancer annotation"]  # optional, as an agent would pass them

Reports hit@1, hit@3, hit@10 and MRR@10 for bm25, vector and hybrid retrieval.
"""

from __future__ import annotations

import statistics
import time
import tomllib
from pathlib import Path

from .db import connect
from .search import search

MODES = ("bm25", "vector", "rrf", "hybrid", "hybrid+variants", "hybrid+variants+rerank")
K = 10


def run(path: Path, modes: tuple[str, ...] = MODES) -> str:
    data = tomllib.loads(path.read_text())
    cases = data.get("case", [])
    # ignore chunks written after the cases were (e.g. the session that wrote the eval set)
    default_until = data.get("until")
    con = connect(readonly=True)
    rows = []
    for mode in modes:
        ranks, lat = [], []
        for c in cases:
            expect = [e.lower() for e in c["expect"]]
            projects = [c["project"]] if c.get("project") else None
            t = time.perf_counter()
            base, *extra = mode.split("+")
            res = search(
                con,
                c["query"],
                k=K,
                projects=projects,
                mode=base,
                rerank="rerank" in extra,
                variants=c.get("variants") if "variants" in extra else None,
                until=c.get("until") or default_until,
                knowledge="knowledge" in extra,
            )
            lat.append((time.perf_counter() - t) * 1000)
            rank = next((i + 1 for i, r in enumerate(res) if any(e in r["text"].lower() for e in expect)), None)
            ranks.append(rank)
        n = len(ranks) or 1

        def hit(k, ranks=ranks, n=n):
            return sum(1 for r in ranks if r and r <= k) / n

        mrr = sum(1 / r for r in ranks if r) / n
        rows.append((mode, hit(1), hit(3), hit(10), mrr, statistics.median(lat) if lat else 0))
    con.close()
    out = [f"{len(cases)} cases, k={K}", "", "| retrieval | hit@1 | hit@3 | hit@10 | MRR@10 | p50 latency |",
           "|---|---:|---:|---:|---:|---:|"]  # fmt: skip
    for mode, h1, h3, h10, mrr, ms in rows:
        out.append(f"| {mode} | {h1:.0%} | {h3:.0%} | {h10:.0%} | {mrr:.2f} | {ms:.0f} ms |")
    return "\n".join(out)


BUDGET = 8000  # chars an agent would read from one tool call


JUDGE_PROMPT = """You check whether a text states given facts. For each numbered fact answer true if the text
states it (paraphrase is fine, the key values must match) and false otherwise. Output ONLY a JSON array of
booleans, one per fact, in order.

Facts:
{facts}

Text:
{text}
"""


def _judge(notes: list[str], text: str) -> float:
    import json

    from .digest import _call_claude

    if not text.strip():
        return 0.0
    facts = "\n".join(f"{i + 1}. {n}" for i, n in enumerate(notes))
    out = _call_claude("", prompt=JUDGE_PROMPT.format(facts=facts, text=text))
    marks = json.loads(out[out.find("[") : out.rfind("]") + 1])
    return sum(1 for m in marks if m is True) / len(notes)


def run_entities(path: Path, judge: bool = False, profiles: bool = False) -> str:
    """'What do we know about X' cases: share of expected facts present in one tool call's output.

    Compares search_history (k=8, as the MCP tool formats it) with the knowledge tool's entity page,
    both cut to the same character budget.
    """
    from .knowledge import ensure, page
    from .server import _fmt

    data = tomllib.loads(path.read_text())
    cases = data.get("case", [])
    con = connect()
    ensure(con)
    rows = {"search_history": [], "knowledge": [], "knowledge→search": []}
    for c in cases:
        expect = [e.lower() for e in c["expect"]]
        projects = [c["project"]] if c.get("project") else None
        res = search(con, c["query"], k=8, projects=projects, variants=c.get("variants"), until=data.get("until"))
        s_out = "\n\n".join(_fmt(r, False) for r in res)[:BUDGET]
        k_out = page(con, c.get("entity") or c["query"], projects, c.get("variants"), BUDGET, profiles) or ""
        notes = c.get("notes")

        def recall(text, expect=expect, notes=notes):
            if judge and notes:
                return _judge(notes, text)
            t = text.lower()
            return sum(1 for x in expect if x in t) / len(expect)

        rows["search_history"].append(recall(s_out))
        rows["knowledge"].append(recall(k_out))
        rows["knowledge→search"].append(rows["knowledge"][-1] if k_out else rows["search_history"][-1])
        print(
            f"  {c.get('entity')}: search {rows['search_history'][-1]:.0%}, "
            f"knowledge {rows['knowledge'][-1]:.0%} ({k_out[:40].splitlines()[0] if k_out else 'not found'})"
        )
    con.close()
    scoring = "LLM judge" if judge else "substring"
    out = [f"{len(cases)} entities, budget {BUDGET} chars, scored by {scoring}", "",
           "| tool | facts recalled |", "|---|---:|"]  # fmt: skip
    out += [f"| {k} | {statistics.mean(v):.0%} |" for k, v in rows.items() if v]
    return "\n".join(out)
