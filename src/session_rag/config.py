"""Paths, user config and project discovery.

A *project* groups one or more Claude Code transcript folders (``~/.claude/projects/<slug>``)
plus optional markdown docs. Projects are discovered automatically from the ``cwd`` recorded in
transcripts; ``~/.config/session-rag/config.toml`` can rename them, merge folders (worktrees,
subdirectories) and add docs.

Example config::

    [projects.shop]
    paths = ["~/code/shop"]              # every session started in or below these dirs
    docs = ["docs/**/*.md", "plans/*.md"] # globs relative to the first path
    knowledge = true                      # optional: keep the knowledge graph current (uses `claude -p`)
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(os.environ.get("SESSION_RAG_HOME", Path.home() / ".local/share/session-rag")).expanduser()
CONFIG_FILE = Path(os.environ.get("SESSION_RAG_CONFIG", Path.home() / ".config/session-rag/config.toml")).expanduser()
CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR", Path.home() / ".claude")).expanduser()
TRANSCRIPTS_DIR = CLAUDE_DIR / "projects"

DEFAULT_DOCS = ["CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md"]
EXCLUDE_PARTS = {"node_modules", ".git", ".venv", "venv", "vendor", "dist", "build", "__pycache__"}


@dataclass
class Project:
    name: str
    paths: list[Path] = field(default_factory=list)
    transcript_dirs: list[Path] = field(default_factory=list)
    docs: list[str] = field(default_factory=list)
    knowledge: bool = False  # keep the knowledge graph current on every index run (sends text to Anthropic)

    def doc_files(self) -> list[Path]:
        out: set[Path] = set()
        for d in self.transcript_dirs:  # Claude Code auto-memory
            out.update(p for p in (d / "memory").glob("*.md") if p.is_file())
        root = self.paths[0] if self.paths else None
        for pattern in self.docs or DEFAULT_DOCS:
            p = Path(pattern).expanduser()
            if p.is_absolute():
                base, pat = p.parent, p.name
            elif root:
                base, pat = root, pattern
            else:
                continue
            for f in base.glob(pat):
                if f.is_file() and not (set(f.parts) & EXCLUDE_PARTS):
                    out.add(f)
        return sorted(out)

    def transcript_files(self) -> list[Path]:
        return sorted(f for d in self.transcript_dirs for f in d.rglob("*.jsonl"))


def slugify(path: str | Path) -> str:
    """Claude Code's folder name for a working directory."""
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


def _load_user_config() -> dict:
    if CONFIG_FILE.exists():
        return tomllib.loads(CONFIG_FILE.read_text())
    return {}


def _cwd_of(slug_dir: Path) -> Path | None:
    """Read the working directory recorded in the first transcript lines."""
    cache = DATA_DIR / "cwd-cache.json"
    try:
        known = json.loads(cache.read_text())
    except (OSError, ValueError):
        known = {}
    if slug_dir.name in known:
        return Path(known[slug_dir.name]) if known[slug_dir.name] else None
    cwd = None
    for f in sorted(slug_dir.glob("*.jsonl"), key=lambda p: p.stat().st_size)[:5]:
        with open(f, errors="replace") as fh:
            for _, line in zip(range(200), fh, strict=False):
                m = re.search(r"\"cwd\"\s*:\s*\"([^\"]+)\"", line)
                if m:
                    cwd = m.group(1)
                    break
        if cwd:
            break
    known[slug_dir.name] = cwd
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(known))
    return Path(cwd) if cwd else None


def _under(path: Path, roots: list[Path]) -> bool:
    return any(path == r or r in path.parents for r in roots)


@lru_cache(maxsize=1)
def projects() -> dict[str, Project]:
    cfg = _load_user_config().get("projects", {})
    result: dict[str, Project] = {}
    for name, c in cfg.items():
        paths = [Path(p).expanduser().resolve() for p in c.get("paths", [])]
        result[name] = Project(
            name=name, paths=paths, docs=list(c.get("docs", [])), knowledge=bool(c.get("knowledge", False))
        )
    if not TRANSCRIPTS_DIR.is_dir():
        return result
    for slug_dir in sorted(p for p in TRANSCRIPTS_DIR.iterdir() if p.is_dir()):
        cwd = _cwd_of(slug_dir)
        cwd = cwd.resolve() if cwd else None
        owner = None
        if cwd:
            owner = next((p for p in result.values() if p.paths and _under(cwd, p.paths)), None)
        if owner is None:
            # also match configured paths by slug prefix (cwd may be missing in tiny transcripts)
            owner = next(
                (p for p in result.values() if any(slug_dir.name.startswith(slugify(r)) for r in p.paths)),
                None,
            )
        if owner is None:
            base = cwd.name if cwd else slug_dir.name.strip("-").split("-")[-1]
            name = base or slug_dir.name
            while name in result:
                name += "-"
            owner = result[name] = Project(name=name, paths=[cwd] if cwd else [])
        owner.transcript_dirs.append(slug_dir)
    return result


def resolve_project(hint: str | None = None) -> str | None:
    """Project name for an explicit name, a path, or the current directory."""
    ps = projects()
    if hint and hint in ps:
        return hint
    path = Path(hint or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()).expanduser().resolve()
    best, depth = None, -1
    for p in ps.values():
        for r in p.paths:
            if _under(path, [r]) and len(r.parts) > depth:
                best, depth = p.name, len(r.parts)
    return best
