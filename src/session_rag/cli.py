"""Command line: ``session-rag <command>``."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from . import __version__, config


def _cmd_index(a) -> None:
    from .index import run

    if a.background:
        # detach so a SessionEnd hook never blocks Claude Code
        args = [sys.executable, "-m", "session_rag.cli", "index", "--quiet", *a.project]
        log = open(config.DATA_DIR / "index.log", "a") if config.DATA_DIR.exists() else subprocess.DEVNULL
        subprocess.Popen(args, stdout=log, stderr=log, start_new_session=True)
        return
    log = (lambda *_: None) if a.quiet else print
    run(a.project or None, rebuild=a.rebuild, log=log)
    if not a.no_knowledge:
        from .knowledge import update

        update(log=log)


def _cmd_search(a) -> None:
    from .db import connect
    from .search import search

    name = None if a.all else (config.resolve_project(a.project) or a.project)
    projects = [name] if name else None
    con = connect(readonly=True)
    for r in search(
        con, a.query, k=a.k, projects=projects, since=a.since, mode=a.mode, rerank=a.precise, variants=a.also
    ):
        text = r["text"].replace("\n", " ")[: a.width]
        print(f"#{r['id']} {r['project']} {(r['ts'] or '')[:16]} {r['role']:9} {text}")


def _cmd_serve(a) -> None:
    if (config.DATA_DIR / "index.db").exists():
        os.environ.setdefault("HF_HUB_OFFLINE", "1")  # model is cached once an index exists
    from .server import serve

    serve(a.project, http=a.http, host=a.host, port=a.port)


def _cmd_projects(a) -> None:
    from .db import connect

    counts = {}
    if (config.DATA_DIR / "index.db").exists():
        con = connect(readonly=True)
        counts = dict(con.execute("SELECT project, count(*) FROM chunks GROUP BY project"))
    for p in config.projects().values():
        print(f"{p.name}: {len(p.transcript_dirs)} transcript dirs, {len(p.doc_files())} docs, "
              f"{counts.get(p.name, 0):,} chunks  {', '.join(map(str, p.paths))}")  # fmt: skip


def _cmd_stats(a) -> None:
    from .stats import report

    print(report(a.since))


def _cmd_eval(a) -> None:
    import tomllib

    from .evaluate import run, run_entities

    path = Path(a.cases)
    if any("entity" in c for c in tomllib.loads(path.read_text()).get("case", [])):
        print(run_entities(path, judge=a.judge, profiles=a.profiles))
    else:
        print(run(path, tuple(a.modes.split(","))))


def _cmd_digest(a) -> None:
    from .digest import run

    n = run(config.resolve_project(a.project) or a.project, since=a.since, until=a.until, today=a.today)
    print(f"{n} digests written")


def _cmd_knowledge(a) -> None:
    from . import knowledge
    from .db import connect

    project = config.resolve_project(a.project) or a.project
    if a.action == "build":
        n = knowledge.build(project, since=a.since, until=a.until)
        print(f"{n} day cards processed")
    elif a.action == "profiles":
        print(f"{knowledge.build_profiles(project, min_facts=a.min_facts)} profiles written")
    elif a.action == "export":
        out = Path(a.out or f"knowledge-{project}").expanduser()
        print(f"{knowledge.export(project, out)} entity pages written to {out}")
    else:
        con = connect(readonly=True)
        out = knowledge.page(con, a.name, [project], budget=20_000)
        if not out:
            raise SystemExit("not found")
        print(out)


def _cmd_schedule(a) -> None:
    from . import schedule

    if a.serve:
        print(schedule.install_server(a.port) if a.action == "install" else schedule.uninstall_server())
    else:
        print(schedule.install(a.minutes) if a.action == "install" else schedule.uninstall())


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="session-rag", description=__doc__)
    ap.add_argument("--version", action="version", version=f"session-rag {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("index", help="index new transcript lines and changed docs")
    s.add_argument("project", nargs="*", help="project names (default: all)")
    s.add_argument("--rebuild", action="store_true", help="drop the index and rebuild from scratch")
    s.add_argument("--quiet", action="store_true")
    s.add_argument("--background", action="store_true", help="run detached (for hooks)")
    s.add_argument("--no-knowledge", action="store_true", help="skip the knowledge graph update")
    s.set_defaults(func=_cmd_index)

    s = sub.add_parser("search", help="search from the terminal")
    s.add_argument("query")
    s.add_argument("--also", action="append", default=[], help="alternative phrasing (repeatable)")
    s.add_argument("-p", "--project", help="project name or path (default: current directory)")
    s.add_argument("-a", "--all", action="store_true", help="search all projects")
    s.add_argument("-k", type=int, default=8)
    s.add_argument("--since")
    s.add_argument("--mode", choices=["hybrid", "bm25", "vector", "rrf"], default="hybrid")
    s.add_argument("--precise", action="store_true", help="rerank with a local cross-encoder (slower)")
    s.add_argument("--width", type=int, default=160)
    s.set_defaults(func=_cmd_search)

    s = sub.add_parser("serve", help="run the MCP server (stdio per session, or --http: one shared server)")
    s.add_argument("-p", "--project", help="default project (stdio: from CLAUDE_PROJECT_DIR / cwd; http: per session)")
    s.add_argument("--http", action="store_true", help="streamable HTTP on --host:--port/mcp instead of stdio")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=47817)
    s.set_defaults(func=_cmd_serve)

    s = sub.add_parser("projects", help="show discovered projects")
    s.set_defaults(func=_cmd_projects)

    s = sub.add_parser("stats", help="usage, adoption and index statistics")
    s.add_argument("--since", help="ISO date (default: first recorded MCP call)")
    s.set_defaults(func=_cmd_stats)

    s = sub.add_parser("eval", help="retrieval quality on a cases file")
    s.add_argument("cases")
    s.add_argument("--modes", default="bm25,vector,rrf,hybrid,hybrid+variants,hybrid+variants+rerank")
    s.add_argument("--judge", action="store_true", help="entity cases: score with an LLM judge via `claude -p`")
    s.add_argument("--profiles", action="store_true", help="entity cases: build missing entity profiles first")
    s.set_defaults(func=_cmd_eval)

    s = sub.add_parser("digest", help="opt-in: summarize sessions per day with `claude -p` (sends text to Anthropic)")
    s.add_argument("project")
    s.add_argument("--since", help="ISO date")
    s.add_argument("--until", help="ISO date (exclusive)")
    s.add_argument("--today", action="store_true", help="also digest the unfinished current day")
    s.set_defaults(func=_cmd_digest)

    s = sub.add_parser("knowledge", help="opt-in: entity/fact graph built with `claude -p` (sends text to Anthropic)")
    s.add_argument("action", choices=["build", "profiles", "show", "export"])
    s.add_argument("project")
    s.add_argument("name", nargs="?", help="entity name (show)")
    s.add_argument("--since", help="ISO date")
    s.add_argument("--until", help="ISO date (exclusive)")
    s.add_argument("--out", help="export directory (default: ./knowledge-<project>)")
    s.add_argument("--min-facts", type=int, default=10, help="profiles: entities with at least this many facts")
    s.set_defaults(func=_cmd_knowledge)

    s = sub.add_parser("schedule", help="install or remove periodic indexing (or, with --serve, the shared MCP server)")
    s.add_argument("action", choices=["install", "uninstall"])
    s.add_argument("--minutes", type=int, default=30, help="indexing interval")
    s.add_argument("--serve", action="store_true", help="the shared HTTP MCP server instead of the indexer")
    s.add_argument("--port", type=int, default=47817)
    s.set_defaults(func=_cmd_schedule)

    a = ap.parse_args(argv)
    a.func(a)


if __name__ == "__main__":
    main()
