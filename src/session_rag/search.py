"""Hybrid retrieval: BM25 (FTS5) + vector KNN (sqlite-vec), fused with Reciprocal Rank Fusion."""

from __future__ import annotations

import sqlite_vec

from .embed import embed_query
from .textproc import fts_query, words

RRF_K = 60
POOL = 80
RERANK_POOL = 20
MIN_LEXICAL = 30  # lexical candidates above which hybrid mode does not run the vector search


def _where(projects, since, roles, alias="c", until=None):
    sql, args = [], []
    if until:
        sql.append(f"{alias}.ts < ?")
        args.append(until)
    if projects:
        sql.append(f"{alias}.project IN ({','.join('?' * len(projects))})")
        args += projects
    if since:
        sql.append(f"{alias}.ts >= ?")
        args.append(since)
    if roles:
        sql.append(f"{alias}.role IN ({','.join('?' * len(roles))})")
        args += roles
    return (" AND " + " AND ".join(sql)) if sql else "", args


def _bm25(con, query, projects, since, roles, pool, until=None):
    fq = fts_query(query)
    if not fq:
        return []
    if projects:
        scope = " OR ".join('"' + p.replace('"', "") + '"' for p in projects)
        fq = f"project:({scope}) AND ({fq})"
    where, args = _where(None, since, roles, until=until)
    if not where:
        rows = con.execute(
            "SELECT rowid FROM chunks_fts WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?", (fq, pool)
        ).fetchall()
    else:
        rows = con.execute(
            f"""SELECT c.id FROM chunks_fts f JOIN chunks c ON c.id = f.rowid
                WHERE chunks_fts MATCH ?{where} ORDER BY f.rank LIMIT ?""",
            [fq, *args, pool],
        ).fetchall()
    return [r[0] for r in rows]


def _vector(con, query, projects, since, roles, pool, until=None):
    qv = sqlite_vec.serialize_float32(embed_query(query))
    filtered = bool(since or roles or until)
    k = pool * 4 if filtered else pool
    ids: list[int] = []
    for proj in projects or [None]:
        if proj is None:
            rows = con.execute(
                "SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ?", (qv, k)
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT rowid, distance FROM chunks_vec WHERE embedding MATCH ? AND k = ? AND project = ?",
                (qv, k, proj),
            ).fetchall()
        ids += [r for r in rows]
    ids.sort(key=lambda r: r[1])
    ids = [r[0] for r in ids]
    if filtered and ids:
        where, args = _where(None, since, roles, until=until)
        q = ",".join("?" * len(ids))
        keep = {r[0] for r in con.execute(f"SELECT c.id FROM chunks c WHERE c.id IN ({q}){where}", [*ids, *args])}
        ids = [i for i in ids if i in keep]
    return ids[:pool]


def _rerank(query: str, rows: list[dict]) -> list[dict]:
    from .embed import rerank_scores

    scores = rerank_scores(query, [r["text"][:1000] for r in rows])
    return [r for _, r in sorted(zip(scores, rows, strict=True), key=lambda x: -x[0])]


# Down-weighting tool output was tried and measured: it hurt (-7 pt hit@10), because exact facts such as
# IPs and IDs often live in command output. Kept as a knob, neutral by default.
ROLE_WEIGHT: dict[str, float] = {}
# Share of query words present in a chunk, blended with BM25 rank. BM25 alone over-rewards one rare term;
# "how many of the asked-about words does this chunk contain" is what made plain grep competitive.
COVERAGE_WEIGHT = 0.5


def _norm(text: str) -> str:
    return " ".join(text.lower().split())[:400]


def search(
    con,
    query: str,
    k: int = 10,
    projects: list[str] | None = None,
    since: str | None = None,
    roles: list[str] | None = None,
    mode: str = "hybrid",
    pool: int = POOL,
    rerank: bool = False,
    variants: list[str] | None = None,
    role_weight: dict[str, float] | None = None,
    dedupe: bool = True,
    until: str | None = None,
    coverage: float | None = None,
    variant_weight: float = 1.0,
    knowledge: bool | None = None,
    fact_weight: float | None = None,
    skip_vectors: bool = True,
) -> list[dict]:
    """Ranked chunks for a query and optional alternative phrasings.

    hybrid (default): lexical lists of all phrasings fused with RRF and weighted by role, then vector hits
    that BM25 missed. Measured on real history, equal-weight lexical+vector fusion hurt precision, so
    vectors only fill the remaining slots. rrf: classic RRF of lexical and vector lists.
    rerank: cross-encoder over the top candidates.
    """
    queries = [q for q in dict.fromkeys([query, *(variants or [])]) if q and q.strip()]
    weights = ROLE_WEIGHT if role_weight is None else role_weight
    lex_lists = [_bm25(con, q, projects, since, roles, pool, until) for q in queries] if mode != "vector" else []
    # in hybrid mode vectors only fill slots BM25 left empty, so skip them (≈1 s on a large index) when it didn't
    lexical_full = mode == "hybrid" and len({i for lst in lex_lists for i in lst}) >= max(k, MIN_LEXICAL)
    vec_lists = (
        [_vector(con, q, projects, since, roles, pool, until) for q in queries]
        if mode != "bm25" and not (lexical_full and skip_vectors)
        else []
    )

    def fuse(lists):
        # the first list is the user's query; alternative phrasings count with VARIANT_WEIGHT
        scores: dict[int, float] = {}
        for n, ranked in enumerate(lists):
            w = 1.0 if n % len(queries) == 0 else variant_weight
            for rank, i in enumerate(ranked):
                scores[i] = scores.get(i, 0.0) + w / (RRF_K + rank)
        return scores

    primary = fuse(lex_lists + vec_lists) if mode == "rrf" else fuse(lex_lists or vec_lists)
    coverage_weight = COVERAGE_WEIGHT if coverage is None else coverage
    fill = (
        []
        if mode in ("rrf", "bm25", "vector")
        else [i for i in sorted(fuse(vec_lists), key=fuse(vec_lists).get, reverse=True)]
    )
    cand = list(primary) + [i for i in fill if i not in primary]
    if not cand:
        return []
    q = ",".join("?" * len(cand))
    meta = {
        r[0]: r
        for r in con.execute(
            f"SELECT id, project, source, session, ts, role, sidechain, text FROM chunks WHERE id IN ({q})", cand
        )
    }
    top = max(primary.values(), default=1.0)
    qwords = [{w for w in words(q) if len(w) > 2} for q in queries]

    def score(i: int) -> float:
        if i not in meta or i not in primary:
            return 0.0
        base = primary[i] / top
        if coverage_weight:
            have = set(words(meta[i][7]))
            cov = max((len(q & have) / len(q) for q in qwords if q), default=0.0)
            base = coverage_weight * cov + (1 - coverage_weight) * base
        return base * weights.get(meta[i][5], 1.0)

    ranked = sorted(primary, key=score, reverse=True)
    ranked += [i for i in fill if i not in primary]

    want = max(k, RERANK_POOL) if rerank else k
    out, seen = [], set()
    for i in ranked:
        r = meta.get(i)
        if not r:
            continue
        if dedupe:
            key = _norm(r[7])
            if key in seen:
                continue
            seen.add(key)
        out.append(
            dict(id=r[0], project=r[1], source=r[2], session=r[3], ts=r[4], role=r[5], sidechain=bool(r[6]), text=r[7],
                 score=score(i))
        )  # fmt: skip
        if len(out) >= want:
            break
    if rerank and out:
        out = _rerank(query, out)
    if knowledge is not False and mode not in ("bm25", "vector", "rrf"):
        out = _with_facts(con, out, queries, projects, until, FACT_WEIGHT if fact_weight is None else fact_weight)
    return out[:k]


# Facts from the knowledge graph (if built) compete with chunks on the same 0..1 score, times this weight.
# 1.0 helped project B (+6 pt hit@1) but cost project A 4-6 pt; 0.7 keeps B +3 and A unchanged.
FACT_WEIGHT = 0.7
FACTS_PER_QUERY = 5


def _with_facts(con, out, queries, projects, until, weight):
    from .knowledge import search_facts

    facts = search_facts(con, queries, projects, until)[:FACTS_PER_QUERY]
    if not facts:
        return out
    by_id = {r["id"]: r for r in out}
    items = list(out)
    for f in facts:
        head = f"[fact] {f['entity']}: {f['fact']}"
        anchor = by_id.get(f["chunk"]) if f["chunk"] else None
        if anchor:  # the evidence chunk is already a hit: annotate and lift it
            anchor["text"] = head + "\n---\n" + anchor["text"]
            anchor["score"] = max(anchor["score"], f["score"] * weight)
            continue
        row = (
            con.execute(
                "SELECT id, project, source, session, ts, role, sidechain, text FROM chunks WHERE id = ?", (f["chunk"],)
            ).fetchone()
            if f["chunk"]
            else None
        )
        text = head + ("\n---\n" + row[7] if row else "")
        item = dict(id=row[0] if row else None, project=f["project"], source=row[2] if row else "knowledge",
                    session=f["session"], ts=f["ts"], role="fact", sidechain=False, text=text,
                    score=f["score"] * weight)  # fmt: skip
        if row:
            by_id[row[0]] = item
        items.append(item)
    items.sort(key=lambda r: r["score"], reverse=True)
    return items


def context(con, chunk_id: int, before: int = 4, after: int = 4) -> list[dict]:
    row = con.execute("SELECT source FROM chunks WHERE id = ?", (chunk_id,)).fetchone()
    if not row:
        return []
    cols = "SELECT id, ts, role, text FROM chunks WHERE source = ?"
    prev = con.execute(f"{cols} AND id < ? ORDER BY id DESC LIMIT ?", (row[0], chunk_id, before)).fetchall()
    cur = con.execute(f"{cols} AND id = ?", (row[0], chunk_id)).fetchall()
    nxt = con.execute(f"{cols} AND id > ? ORDER BY id LIMIT ?", (row[0], chunk_id, after)).fetchall()
    rows = list(reversed(prev)) + cur + nxt
    return [dict(id=r[0], ts=r[1], role=r[2], text=r[3], current=r[0] == chunk_id) for r in rows]
