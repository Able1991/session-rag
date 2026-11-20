# session-rag

[![CI](https://github.com/Able1991/session-rag/actions/workflows/ci.yml/badge.svg)](https://github.com/Able1991/session-rag/actions/workflows/ci.yml)
[![Security](https://github.com/Able1991/session-rag/actions/workflows/security.yml/badge.svg)](https://github.com/Able1991/session-rag/actions/workflows/security.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue)
![MCP](https://img.shields.io/badge/MCP-server-8A2BE2)
![Local](https://img.shields.io/badge/runs-100%25%20local-success)

**Private, local search over your past Claude Code sessions.** Claude Code keeps every conversation as
JSONL under `~/.claude/projects`, but a new session can't see any of it. session-rag indexes that history
and your project docs and gives Claude an MCP tool to search it. So "how did we fix the load balancer
last month?" gets the actual command and conclusion, not a guess.

| | |
|---|---|
| answer ranked first | **67%** vs 37% for grep (hand-written questions, infra project) |
| answer in the top 3 | **87%** |
| response size | ~2 KB per call instead of 100k+ tokens of raw JSONL |
| search latency | ~0.7 s median on a 970k-chunk, 16-project index |
| incremental reindex | ~3 s |
| leaves your machine | nothing (opt-in knowledge graph and digests call Claude) |

**Quick start** ([по-русски](docs/overview-ru.md))

```bash
git clone https://github.com/Able1991/session-rag && cd session-rag && uv sync
uv run session-rag index
claude mcp add --scope user history -e HF_HUB_OFFLINE=1 -- "$PWD/.venv/bin/session-rag" serve
```

- **Local and private.** Search makes no LLM calls, uses no cloud embeddings and sends no telemetry.
  Secrets (API keys, tokens, passwords, credentials in URLs) are masked *before* they reach the index.
  Only the opt-in `digest` and `knowledge` commands send text to Claude, through your own `claude` CLI.
- **Built for the facts you actually look up.** IPs, pod names, error strings, ticket numbers, file paths.
  Claude sends the question plus a couple of alternative phrasings. Ranking blends stemmed BM25
  (Russian and English Snowball) with how many of the asked-about words a chunk contains, and local
  embeddings fill the gaps. Results are measured on real history and published below, including what
  didn't work.
- **Cheap to keep current.** Transcripts are append-only, so indexing resumes from the last byte offset.
  The full history re-indexes in minutes, and an update takes seconds.
- **Zero-config.** Projects are discovered from the working directory recorded in transcripts. Subagent
  transcripts, Claude Code auto-memory and `CLAUDE.md` files are included.

## How it works

```
~/.claude/projects/**/*.jsonl ─┐                       ┌─ FTS5 (stemmed BM25, per-project)
CLAUDE.md, memory, docs ───────┼─ parse → redact ─────┤
                               │  (chunks)             └─ sqlite-vec (local embeddings)
                               │
Claude Code ── MCP: search_history(query, also=[…]) / get_context ── BM25 + coverage, vectors fill gaps
```

Each chunk is one meaningful unit: a user message, an assistant answer, a tool call (for Bash, the
description plus the command), a truncated tool result, or a markdown section. Everything lives in one
SQLite file (`~/.local/share/session-rag/index.db`, mode 600).

## Results

Measured on real history: 16 projects, 749k chunks, a 1.9 GB index at the time, on an Apple M-series laptop.

**Retrieval quality.** Two test sets from two projects, written in plain words rather than copied
from the answer. A hit is a result containing the known answer string. Chunks written after a set was
created are excluded, so the session that wrote it can't leak answers.

- *project A* (infrastructure and ops, mostly Russian): 30 questions written by hand.
- *project B* (product code, a lot of English): 33 questions generated from randomly sampled assistant
  messages of one week.

For both sets, a separate agent that never saw the answers wrote 2 alternative phrasings per question,
the way Claude passes them in `also`.

| retrieval | project A hit@1 / hit@3 / hit@10 / MRR | project B hit@1 / hit@3 / hit@10 / MRR |
|---|---|---|
| grep: chunks containing all query words | 0% / 0% / 0% / 0.00 | n/a |
| grep: any query word, most matching words first | 37% / 50% / 80% / 0.48 | n/a |
| single query, stemmed BM25 | 43% / 60% / 77% / 0.53 | 24% / 64% / 85% / 0.45 |
| **default**: query + agent phrasings, BM25 blended with query coverage | **67% / 87% / 90% / 0.76** | 21% / 58% / 82% / 0.42 |

The honest summary: alternative phrasings plus coverage ranking help a lot on one project and cost a
little on the other. On average MRR is 0.59 vs 0.49 for a single query. English phrasings drag noisy
matches out of code-heavy history. Your mileage will vary. Run `session-rag eval` on your own
questions.

The grep baselines run on the same clean chunks, which flatters them. Real grep over raw transcripts
returns whole JSONL lines: one message with its tool output can exceed 60 KB. A single keyword such as an
IP address returned 32 lines and 360 KB, about 100k tokens. `search_history` returns about 2 KB.

What moved the numbers, and what didn't:

| change | effect |
|---|---|
| Russian/English Snowball stemming | +6 pt hit@1 on inflected Russian queries |
| project filter inside the FTS index | latency 755 ms → ~15 ms on the full 749k-chunk index |
| alternative phrasings (`also`) | project A hit@3 60% → 83%; project B hit@10 82% → 73% at equal weight; weighting the original query higher did not fix both at once |
| ranking by share of query words present ("coverage") | project A hit@1 +10–17 pt with phrasings; neutral to slightly negative on project B; default weight 0.5 |
| multilingual embeddings (potion, MiniLM) | did not help: about one third of answers in the top 10 when used alone, and equal-weight RRF *lowered* hit@1 from 50% to 20%; vectors only fill slots BM25 left empty |
| down-weighting tool output vs. assistant answers | hurt (−7 pt hit@10): IPs and IDs live in command output |
| cross-encoder rerank (jina-reranker-v2 multilingual) | +3 pt on a single query, worse after coverage ranking, ~6 s per query; CLI only (`--precise`) |
| per-day session digests by Claude Haiku (`session-rag digest`, opt-in) | no change on fact lookups: the answer was in a digest for 17 of 33 questions, but the shorter raw chunk always ranked higher. Digests target "what did we do last week and why" questions, which these sets don't measure |
| knowledge graph facts fused into `search_history` (`session-rag knowledge build`, opt-in) | at full weight: project B hit@1 18% → 24% but project A 67% → 63% and 50% → 44% (hard set); default weight 0.7: project B 21%, project A unchanged. Fact lookups gain little; the graph pays off on entity questions (below) |

**Knowledge graph (opt-in pilot).** `session-rag knowledge build <project>` has Claude Haiku read each day
of each session and return entities, one-sentence facts with exact values, and relations. Every fact keeps
the id of the chunk it came from. Nodes with the same name merge, and entities with 15+ facts get an
LLM-written profile page. The `knowledge(name)` MCP tool returns that page plus the newest facts, and
`knowledge export` writes one linked markdown page per entity (an Obsidian vault).

Pilot on project B, two months of history: 378 session-day segments, 368 parsed (10 returned no JSON),
12.3k facts, 6k entity nodes, about 5 h of Haiku time at 4 calls in parallel (~80 min wall clock).
A separate set of 12 "what do we know about X" questions was written blind from raw transcripts, with 5
facts each. An LLM judge counted how many of those facts one tool call returns within 8,000 characters:

| tool, one call, 8k chars | facts recalled |
|---|---:|
| `search_history` (k=8, with phrasings) | 12% |
| `knowledge` without profiles (newest facts) | 13–27% |
| `knowledge` with profiles | 38–42% (substring: 38% vs 20% for search) |

Entity resolution matters most. A node chosen by fact count instead of by the asked name, or a cluster cut
to its newest facts, erased most of the gain. Exact-substring scoring gives lower numbers for both tools,
because the facts are paraphrased.

**Indexing.** 4,885 transcripts (6.7 GB of JSONL) become 749k chunks. A full build from scratch takes
6.5 min: parsing, redaction and FTS take 4.7 min, and embeddings take 1.8 min at about 7k chunks/s on CPU.
Peak memory is 1.6 GB. An incremental run with nothing new takes 2.6 s.

**Usage in practice.** Three weeks of daily use by one developer across 16 projects (November 2026,
`session-rag stats`):

| | |
|---|---|
| index | 973k chunks, 2.5 GB |
| tool calls | 398 over 20 active days, about 20 per day |
| `search_history` | 342 calls, p50 0.7 s, p95 6 s (cold model load), ~1.9k tokens per answer |
| `get_context` | 39 calls, p50 8 ms |
| `knowledge` | 17 calls, p50 0.16 s |
| sessions that used it | almost every long session where the server was connected |

The honest part: agents still grep raw transcripts alongside it (tens of greps per project over the
period), mostly when a session started before the server was reachable or after context compaction.
The knowledge graph was called rarely for what it costs in Haiku calls, so keep it off unless you ask
many "what do we know about X" questions.

## Install

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+. macOS or Linux.

### As a Claude Code plugin (recommended)

```text
/plugin marketplace add Able1991/session-rag
/plugin install session-rag@session-rag
```

The plugin registers the `history` MCP server, a skill that tells Claude when to search history, and a
`SessionEnd` hook that indexes new messages in the background. Build the index once:

```bash
uvx --from git+https://github.com/Able1991/session-rag session-rag index
```

The first run downloads the embedding model (~0.5 GB) into `~/.local/share/session-rag/models`.

### Manually

```bash
git clone https://github.com/Able1991/session-rag && cd session-rag
uv sync
uv run session-rag index            # build the index
uv run session-rag schedule install # optional: reindex every 30 min (launchd / systemd)
```

Register the server once for all projects:

```bash
claude mcp add --scope user history -e HF_HUB_OFFLINE=1 -- "$PWD/.venv/bin/session-rag" serve
```

Or add it to a single project's `.mcp.json`:

```json
{
  "mcpServers": {
    "history": {
      "command": "/path/to/session-rag/.venv/bin/session-rag",
      "args": ["serve"],
      "env": { "HF_HUB_OFFLINE": "1" }
    }
  }
}
```

The server picks the current project from `CLAUDE_PROJECT_DIR` / the working directory. Pass
`"args": ["serve", "--project", "name"]` to pin it.

### One shared server for the whole machine

Every Claude Code session started this way runs its own copy of the server, and each copy loads the embedding
model (about 1 GB). With a dozen sessions open that is more memory than a laptop has. Run one server instead:

```bash
session-rag schedule install --serve          # launchd / systemd unit, kept alive, http://127.0.0.1:47817/mcp
```

and register it as an HTTP server:

```json
{ "mcpServers": { "history": { "type": "http", "url": "http://127.0.0.1:47817/mcp" } } }
```

The project of a call is the working folder the session reports as its MCP root (Claude Code does), the `project`
argument overrides it. The model is loaded once, limited to two threads (`SESSION_RAG_THREADS`), and dropped after
15 minutes without calls (`SESSION_RAG_IDLE_UNLOAD`, seconds, 0 keeps it). Searches use read-only SQLite
connections, so the periodic indexer keeps running as its own job next to the server.

Without the plugin, add a line to `CLAUDE.md` so Claude reaches for the tool:

> When a question is about past work (how something was fixed, which IP/path/command, what was decided),
> call `search_history` first, then `get_context(id)`; grep transcripts only if it finds nothing.

## Use

Claude calls the tools on its own. Typical calls:

```text
search_history(query="how did we fix ingress health checks", also=["octavia pool node-selector", "балансировщик health-check ноды"])
search_history(query="what did support answer about the S3 timeout", roles="assistant,user")
search_history(query="egress IP", project="all", since="2026-10-01")
get_context(chunk_id=17547)
```

From the terminal:

```bash
session-rag search "черный экран на эфире" -k 5     # current project, any language
session-rag search "terraform state lock" --all
session-rag projects                                 # discovered projects and chunk counts
session-rag stats                                    # usage and adoption
session-rag eval my-cases.toml                       # measure retrieval on your own questions
```

## Configuration

Optional `~/.config/session-rag/config.toml` to name projects, merge folders and add docs:

```toml
[projects.shop]
paths = ["~/code/shop"]                       # sessions started in or below these dirs
docs  = ["docs/**/*.md", "*/CLAUDE.md"]       # globs relative to the first path
```

| Environment variable | Default | |
|---|---|---|
| `SESSION_RAG_HOME` | `~/.local/share/session-rag` | index, models, logs |
| `SESSION_RAG_MODEL` | `minishlab/potion-multilingual-128M` | any fastembed text model; reindex with `--rebuild` |
| `SESSION_RAG_RERANKER` | `jinaai/jina-reranker-v2-base-multilingual` | cross-encoder for `session-rag search --precise` |
| `SESSION_RAG_NO_USAGE_LOG` | unset | disable the local usage log |
| `CLAUDE_CONFIG_DIR` | `~/.claude` | where transcripts are read from |

## Measuring on your own history

Write a few questions you know the answers to:

```toml
[[case]]
query = "how did we fix the flaky upload timeouts"
project = "shop"          # optional
expect = ["ETIMEDOUT", "retry"]   # any chunk containing one of these counts as a hit
```

`session-rag eval cases.toml` prints hit@1/3/10, MRR and latency for every retrieval mode. Paraphrase
the questions; copying words from the answer flatters lexical search.

## Privacy and limits

- Redaction is pattern-based. It catches common key formats and `key=value`/JSON/URL credentials, but it
  can't promise to catch every secret. The index file is created with mode 600 in a 700 directory. Treat
  it like the transcripts it is built from.
- Search results are history, not truth. The skill and tool description tell Claude to verify against
  the current state before acting.
- Code is not indexed on purpose. For code, grep, LSP and reading files are always current, while an
  index goes stale with every commit.

## Security

Every push to `main` and a weekly schedule run Gitleaks over the full history, Semgrep, CodeQL (Python and the
workflows) and pip-audit over the locked dependencies; any critical finding turns the Security badge red. Actions
are pinned by SHA and Dependabot keeps them and the Python dependencies current. Threat model and how to report a
vulnerability: [SECURITY.md](SECURITY.md).

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
```

Tests use a deterministic hash embedder (`SESSION_RAG_MODEL=hash`), so they need no model download. See
[CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE)
