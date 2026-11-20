# Changelog

## 0.1.0

- `knowledge` (opt-in): entity/fact/relation graph per session day via `claude -p`, facts anchored to chunks;
  fact hits fused into `search_history`; `knowledge` MCP tool with LLM-written profiles of large entities;
  `knowledge export` to linked markdown; `eval --judge` for "what do we know about X" cases.
- `knowledge = true` per project: every index run (schedule, SessionEnd hook) adds finished days and refreshes
  grown profiles, one runner at a time; the schedule passes PATH so `claude` is found. Extraction prompt fences
  the transcript so the model reads it instead of continuing it.
- `digest` (opt-in): per-day session digests via `claude -p --no-session-persistence`.
- Variant weight knob; coverage weight 0.5 after evaluating on two projects.
- `search_history(also=[...])`: alternative phrasings from the agent, fused per phrasing.
- Ranking blends stemmed BM25 with query-word coverage; duplicate chunks are dropped.
- `eval`: grep-free cutoff (`until`), per-case `variants`.

- Incremental indexing of Claude Code transcripts (append-only offsets) and markdown docs, including auto-memory.
- Automatic project discovery from transcript `cwd`, optional `~/.config/session-rag/config.toml`.
- Retrieval: stemmed SQLite FTS5 (BM25) with in-index project filter, sqlite-vec KNN to fill gaps; date and role filters.
- Local multilingual embeddings (fastembed, potion-multilingual-128M); no network after model download.
- Secret redaction before indexing.
- MCP server (`search_history`, `get_context`), Claude Code plugin with skill and SessionEnd hook.
- `stats` (usage, adoption, index size) and `eval` (hit@k, MRR for bm25 / vector / hybrid).
- launchd / systemd scheduling.
