# Contributing

Issues and pull requests are welcome. Please follow the [Code of Conduct](CODE_OF_CONDUCT.md). For a security problem, see [SECURITY.md](SECURITY.md) instead.

```bash
uv sync
uv run pytest -q
uv run ruff check src tests && uv run ruff format --check src tests
uvx --from semgrep semgrep scan --config p/default --config p/secrets --config p/python \
  --exclude-rule python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query \
  --severity ERROR --error --metrics off
```

- Tests use a deterministic hash embedder (`SESSION_RAG_MODEL=hash`) and need no model download.
- A change to ranking should come with numbers: run `session-rag eval` on your own cases before and after and put both
  in the pull request. Changes that help one history and hurt another are common, see the README.
- Never paste real transcripts, keys or index files into issues or tests. Redaction fixtures use fake keys only.
