import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).parent / "_tmp"


def pytest_configure(config):
    os.environ["SESSION_RAG_MODEL"] = "hash"
    os.environ["SESSION_RAG_NO_USAGE_LOG"] = "1"


def _line(**kw):
    return json.dumps(kw, ensure_ascii=False) + "\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    proj = tmp_path / "work" / "shop"
    proj.mkdir(parents=True)
    (proj / "CLAUDE.md").write_text("# Shop\n\n## Deploy\n\nDeploy with `make release` from the main branch.\n")
    slug = claude / "projects" / ("-" + str(proj).strip("/").replace("/", "-"))
    (slug / "memory").mkdir(parents=True)
    (slug / "memory" / "lb.md").write_text("# Load balancer\n\nHealth checks fixed with a node-selector annotation.\n")
    t = slug / "s1.jsonl"
    t.write_text(
        _line(type="user", sessionId="s1", uuid="u1", timestamp="2026-10-01T10:00:00Z", cwd=str(proj),
              message={"role": "user", "content": "why do uploads to the bucket time out?"})
        + _line(type="assistant", sessionId="s1", uuid="u2", timestamp="2026-10-01T10:01:00Z", cwd=str(proj),
                message={"role": "assistant", "content": [
                    {"type": "tool_use", "id": "t1", "name": "Bash",
                     "input": {"command": "curl -m 5 https://s3.example.net/", "description": "Probe S3"}},
                ]})
        + _line(type="user", sessionId="s1", uuid="u3", timestamp="2026-10-01T10:01:05Z", cwd=str(proj),
                message={"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "t1", "content": "curl: (28) connect ETIMEDOUT 10.9.8.7:443"}
                ]})
        + _line(type="assistant", sessionId="s1", uuid="u4", timestamp="2026-10-01T10:02:00Z", cwd=str(proj),
                message={"role": "assistant", "content": [
                    {"type": "text", "text": "The storage provider blocks our egress IP 10.9.8.7, password=hunter2hunter was rotated."}
                ]})
    )  # fmt: skip
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    monkeypatch.setenv("SESSION_RAG_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("SESSION_RAG_CONFIG", str(tmp_path / "none.toml"))
    import importlib

    import session_rag.config as config

    importlib.reload(config)
    for m in ("db", "index", "search", "stats", "usage"):
        importlib.reload(importlib.import_module(f"session_rag.{m}"))
    return {"project_dir": proj, "transcript": t, "config": config}
