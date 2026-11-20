"""Incremental indexing. Transcripts are append-only, so each file is read from its last offset."""

from __future__ import annotations

import fcntl
import time
from contextlib import contextmanager

import sqlite_vec

from . import config
from .db import SCHEMA_VERSION, connect, delete_source
from .embed import MODEL, embed
from .parse import parse_markdown, parse_transcript
from .redact import redact
from .textproc import stem_text

EMBED_BATCH = 4096


@contextmanager
def _lock(name: str = "index"):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.DATA_DIR / f"{name}.lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def _insert(con, project: str, chunks) -> int:
    n = 0
    for c in chunks:
        text = redact(c.text)
        cur = con.execute(
            "INSERT INTO chunks(project, source, session, uuid, ts, role, sidechain, text) VALUES (?,?,?,?,?,?,?,?)",
            (project, c.source, c.session, c.uuid, c.ts, c.role, int(c.sidechain), text),
        )
        con.execute(
            "INSERT INTO chunks_fts(rowid, stem, project) VALUES (?, ?, ?)", (cur.lastrowid, stem_text(text), project)
        )
        n += 1
    return n


def _record(con, path, project, kind, offset, st) -> None:
    con.execute(
        "INSERT OR REPLACE INTO files(path, project, kind, offset, mtime, size) VALUES (?,?,?,?,?,?)",
        (str(path), project, kind, offset, st.st_mtime, st.st_size),
    )


def index_project(con, p: config.Project) -> int:
    known = {
        r[0]: r[1:] for r in con.execute("SELECT path, offset, mtime, size FROM files WHERE project = ?", (p.name,))
    }
    added = 0
    for f in p.transcript_files():
        try:
            st = f.stat()
        except FileNotFoundError:  # transcript removed while indexing
            continue
        prev = known.get(str(f))
        offset = 0
        if prev:
            off, mtime, size = prev
            if size == st.st_size and mtime == st.st_mtime:
                continue
            offset = off if st.st_size >= size else 0
            if offset == 0:
                delete_source(con, str(f))
        try:
            chunks, new_off = parse_transcript(f, offset)
        except FileNotFoundError:
            continue
        added += _insert(con, p.name, chunks)
        _record(con, f, p.name, "transcript", new_off, st)

    docs = p.doc_files()
    for f in docs:
        try:
            st = f.stat()
        except FileNotFoundError:
            continue
        prev = known.get(str(f))
        if prev and prev[1] == st.st_mtime and prev[2] == st.st_size:
            continue
        delete_source(con, str(f))
        try:
            added += _insert(con, p.name, parse_markdown(f))
        except OSError:
            continue
        _record(con, f, p.name, "doc", 0, st)
    live = {str(f) for f in docs}
    for (path,) in con.execute("SELECT path FROM files WHERE project = ? AND kind = 'doc'", (p.name,)).fetchall():
        if path not in live:
            delete_source(con, path)
            con.execute("DELETE FROM files WHERE path = ?", (path,))
    con.commit()
    return added


def embed_pending(con) -> int:
    done = 0
    while True:
        batch = con.execute(
            "SELECT id, project, text FROM chunks WHERE embedded = 0 LIMIT ?", (EMBED_BATCH,)
        ).fetchall()
        if not batch:
            return done
        vecs = embed([t for _, _, t in batch])
        con.executemany(
            "INSERT OR REPLACE INTO chunks_vec(rowid, project, embedding) VALUES (?, ?, ?)",
            [(i, proj, sqlite_vec.serialize_float32(v)) for (i, proj, _), v in zip(batch, vecs, strict=True)],
        )
        con.executemany("UPDATE chunks SET embedded = 1 WHERE id = ?", [(i,) for i, _, _ in batch])
        con.commit()
        done += len(batch)


def run(names: list[str] | None = None, rebuild: bool = False, log=print) -> dict:
    with _lock() as got:
        if not got:
            log("another indexer is running, skipping")
            return {}
        if rebuild:
            for suffix in ("", "-wal", "-shm"):
                (config.DATA_DIR / f"index.db{suffix}").unlink(missing_ok=True)
        con = connect()
        meta = dict(con.execute("SELECT key, value FROM meta"))
        if meta.get("model", MODEL) != MODEL or meta.get("schema", SCHEMA_VERSION) != SCHEMA_VERSION:
            raise SystemExit("index format or embedding model changed: run `session-rag index --rebuild`")
        con.executemany(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)", [("model", MODEL), ("schema", SCHEMA_VERSION)]
        )
        projects = config.projects()
        stats = {}
        for name in names or list(projects):
            if name not in projects:
                log(f"unknown project: {name}")
                continue
            t = time.time()
            added = index_project(con, projects[name])
            stats[name] = {"added": added, "seconds": round(time.time() - t, 1)}
        t = time.time()
        embedded = embed_pending(con)
        stats["_embedded"] = {"chunks": embedded, "seconds": round(time.time() - t, 1)}
        con.close()
        for k, v in stats.items():
            log(f"{k}: {v}")
        return stats
