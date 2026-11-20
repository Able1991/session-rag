# Security

## Reporting a vulnerability

Please report it privately: open [a new security advisory](https://github.com/Able1991/session-rag/security/advisories/new)
(the Security tab of the repository, Report a vulnerability). Do not open a public issue for it.

Say what you found, how to reproduce it and which version you used. You will get an answer within a few days. Once a
fix is released, the advisory is published with credit to you, unless you prefer otherwise. Only the latest version
gets fixes.

## In scope

- Indexing and secret redaction (`src/session_rag/parse.py`, `redact.py`, `index.py`).
- The MCP server: stdio, and the shared HTTP server on `127.0.0.1` (`server.py`).
- The scheduled jobs it installs (`schedule.py`), the Claude Code plugin, hook and skill.
- The build and CI pipeline (`.github/workflows`).

Out of scope: problems in Claude Code, the `claude` CLI or Anthropic's API themselves, and attacks that need your user
account to be already compromised.

## Threat model

- **Your history is sensitive, and so is the index.** Transcripts hold commands, their output, file contents and
  whatever you pasted. The index is built from them: one SQLite file created with mode 600 in a 700 directory
  (`~/.local/share/session-rag`). Treat it like the transcripts themselves.
- **Redaction is a safety net, not a guarantee.** API keys, tokens, JWTs, passwords and credentials in URLs are masked
  before they reach the index, by pattern. A secret in an unusual format can get through. Report formats it misses.
- **Search is local.** No LLM calls, no cloud embeddings, no telemetry. The embedding model is downloaded once from
  Hugging Face; set `HF_HUB_OFFLINE=1` afterwards.
- **Two commands send text out, only when you run them.** `digest` and `knowledge build` pass session text to your own
  `claude` CLI (Anthropic, on your subscription), with no tools and no MCP servers. History is untrusted text for that
  model: a pasted page could carry instructions, so it gets nothing it could act with.
- **Search results are untrusted text for the agent.** They are past conversations, including web pages and tool
  output quoted in them. The tool description and the skill tell Claude to treat them as history to verify, not as
  instructions.
- **The shared HTTP server listens only on `127.0.0.1` and has no password.** Any process of any user on the same
  machine can query it, so on a shared machine use the per-session stdio server instead. Your own user account is
  trusted: anything running as you can read the transcripts directly anyway.
