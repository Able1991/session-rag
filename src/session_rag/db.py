"""SQLite store: chunks + FTS5 (BM25) + sqlite-vec vectors, one file for all projects."""

from __future__ import annotations

import os
import sqlite3

import sqlite_vec

from .config import DATA_DIR
from .embed import DIM

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY,
  project TEXT NOT NULL,
  source TEXT NOT NULL,
  session TEXT,
  uuid TEXT,
  ts TEXT,
  role TEXT,
  sidechain INTEGER DEFAULT 0,
  text TEXT NOT NULL,
  embedded INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chunks_source ON chunks(source);
CREATE INDEX IF NOT EXISTS chunks_project_ts ON chunks(project, ts);
CREATE INDEX IF NOT EXISTS chunks_pending ON chunks(embedded) WHERE embedded = 0;
-- stemmed copy of chunks.text; project as a column so the filter runs inside FTS
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
  stem, project, content='', contentless_delete=1, tokenize='unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(
  project TEXT partition key,
  embedding float[{DIM}]
);
CREATE TABLE IF NOT EXISTS files (
  path TEXT PRIMARY KEY,
  project TEXT,
  kind TEXT,
  offset INTEGER DEFAULT 0,
  mtime REAL,
  size INTEGER
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

SCHEMA_VERSION = "2"


def db_path():
    return DATA_DIR / "index.db"


def connect(readonly: bool = False) -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_DIR, 0o700)
    path = db_path()
    new = not path.exists()
    if readonly and not new:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30)
    else:
        con = sqlite3.connect(path, timeout=60)
        if new:
            os.chmod(path, 0o600)
    con.enable_load_extension(True)
    sqlite_vec.load(con)
    con.enable_load_extension(False)
    if not readonly or new:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        con.executescript(SCHEMA)
    return con


def delete_source(con: sqlite3.Connection, source: str) -> None:
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE source = ?", (source,))]
    for i in range(0, len(ids), 500):
        part = ids[i : i + 500]
        q = ",".join("?" * len(part))
        con.execute(f"DELETE FROM chunks_vec WHERE rowid IN ({q})", part)
        con.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({q})", part)
        con.execute(f"DELETE FROM chunks WHERE id IN ({q})", part)
