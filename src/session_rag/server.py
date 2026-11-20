"""MCP server exposing ``search_history``, ``get_context`` and ``knowledge``.

Two ways to run it:
  stdio          one process per Claude Code session (``session-rag serve``); the default project is the cwd.
  streamable-http one shared process for the machine (``session-rag serve --http``, see ``schedule``): the project
                  comes from the roots the client reports for its session (Claude Code: the working folder), else
                  from the ``project`` argument; the embedding model is loaded once and dropped after idle time.
"""

from __future__ import annotations

import os
import threading
import time
from urllib.parse import unquote, urlparse

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ClientCapabilities, RootsCapability

from . import config, embed, usage
from .db import connect
from .search import context, search

SNIPPET = 900


def _fmt(r: dict, show_project: bool) -> str:
    src = r["source"]
    if r.get("session"):
        src = src.split("/projects/")[-1]
    tag = f"{r['project']} " if show_project else ""
    side = " subagent" if r.get("sidechain") else ""
    text = r["text"] if len(r["text"]) <= SNIPPET else r["text"][:SNIPPET] + "…"
    return f"[#{r['id']} {tag}{(r['ts'] or '')[:16]} {r['role']}{side}] {src}\n{text}"


class _Projects:
    """Default project of a call: the session's workspace when the client reports roots, else the fixed default."""

    def __init__(self, default: str | None, per_session: bool):
        self.default = default
        self.per_session = per_session
        self.cache: dict[int, str | None] = {}
        self.last_call = time.time()

    async def of(self, ctx: Context | None) -> str | None:
        self.last_call = time.time()
        if not self.per_session or ctx is None:
            return self.default
        key = id(ctx.session)
        if key in self.cache:
            return self.cache[key]
        name = self.default
        try:
            if ctx.session.check_client_capability(ClientCapabilities(roots=RootsCapability())):
                with anyio.fail_after(3):
                    roots = (await ctx.session.list_roots()).roots
                for r in roots:
                    u = urlparse(str(r.uri))
                    if u.scheme == "file":
                        found = config.resolve_project(unquote(u.path))
                        if found:
                            name = found
                            break
        except Exception:  # a client without roots: fall back to the explicit argument or everything
            pass
        self.cache[key] = name
        return name


def build(default_project: str | None, per_session: bool = False) -> MCPServer:
    names = sorted(config.projects())
    mcp = MCPServer("session-rag")
    current = _Projects(default_project, per_session)
    mcp.projects = current  # the idle watcher reads last_call
    shown = "the calling session's workspace, else all" if per_session else default_project or "none"

    search_doc = (
        "Search the history of past Claude Code sessions and project docs (CLAUDE.md, memory, configured docs). "
        "Use it FIRST whenever the question is about past work: how something was fixed, which IP/path/command/"
        "setting was used, what was decided, what support or a teammate answered, and after context compaction "
        "when details are needed. Hybrid retrieval: exact tokens (IPs, pod names, IDs, error strings) plus "
        "meaning, works in any language. "
        "Args: query; also = 2-3 alternative phrasings you think the answer was written with (other language, "
        "synonyms, likely config/variable/command names, without guessing concrete values); measured to raise "
        "the right hit into the top 3 noticeably, so always pass them. k = number of results; "
        "since = ISO date like 2026-10-01; roles = comma list of user, "
        "assistant, tool, result, doc (assistant = conclusions and summaries, tool = commands run, "
        "result = command output); project = another project name or 'all' "
        f"(current: {shown}; known: {', '.join(names)}). "
        "Output lines look like [#id time role] source; call get_context(id) for the surrounding messages. "
        "Secrets are masked as ***. History can be outdated: verify against the current state before acting."
    )

    @mcp.tool(description=search_doc)
    async def search_history(
        query: str,
        also: list[str] | None = None,
        k: int = 8,
        since: str = "",
        roles: str = "",
        project: str = "",
        ctx: Context = None,
    ) -> str:
        t = time.perf_counter()
        name = None if project == "all" else project or await current.of(ctx)
        projects = [name] if name else None
        role_list = [x.strip() for x in roles.split(",") if x.strip()] or None

        def work():
            con = connect(readonly=True)
            try:
                return search(con, query, k=k, projects=projects, since=since or None, roles=role_list, variants=also)
            finally:
                con.close()

        res = await anyio.to_thread.run_sync(work)
        out = "\n\n".join(_fmt(r, show_project=projects is None) for r in res) or "No results."
        usage.log_call("search_history", name, (time.perf_counter() - t) * 1000, len(res), len(out), query)
        return out

    @mcp.tool(description="Messages around a search hit (same transcript or document). Args: chunk_id from "
              "search_history, before/after = how many neighbouring chunks to include.")  # fmt: skip
    async def get_context(chunk_id: int, before: int = 4, after: int = 4, ctx: Context = None) -> str:
        t = time.perf_counter()
        name = await current.of(ctx)

        def work():
            con = connect(readonly=True)
            try:
                return context(con, chunk_id, before, after)
            finally:
                con.close()

        rows = await anyio.to_thread.run_sync(work)
        out = (
            "\n\n".join(
                ("▶ " if r["current"] else "") + f"[#{r['id']} {(r['ts'] or '')[:16]} {r['role']}]\n{r['text']}"
                for r in rows
            )
            or "Not found."
        )
        usage.log_call("get_context", name, (time.perf_counter() - t) * 1000, len(rows), len(out))
        return out

    @mcp.tool(description="Everything the knowledge graph knows about one entity (service, host, IP, repo, client, "
              "document, incident...): facts by date (newest first) with the chunk id they came from, and related "
              "entities. Use for 'what do we know about X' / 'how is X connected to Y'. Only for projects where the "
              "graph was built (`session-rag knowledge build`); otherwise use search_history. "
              "Args: name (any alias, either language), also = other names of the same thing (English/Russian, "
              "short name, domain), project (default: current).")  # fmt: skip
    async def knowledge(name: str, also: list[str] | None = None, project: str = "", ctx: Context = None) -> str:
        from .knowledge import page

        t = time.perf_counter()
        proj = project or await current.of(ctx)

        def work():
            con = connect(readonly=True)
            try:
                return page(con, name, [proj] if proj and proj != "all" else None, also=also)
            finally:
                con.close()

        out = await anyio.to_thread.run_sync(work)
        out = out or "Not in the knowledge graph. Try search_history."
        usage.log_call("knowledge", proj, (time.perf_counter() - t) * 1000, out.count("\n- "), len(out), name)
        return out

    return mcp


def _idle_watch(projects: _Projects, idle_s: float) -> None:
    while True:
        time.sleep(60)
        if embed.loaded() and time.time() - projects.last_call > idle_s:
            embed.unload()


def serve(project: str | None, http: bool = False, host: str = "127.0.0.1", port: int = 47817) -> None:
    # load the embedding model in the background so the first search does not pay ~1.7 s for it
    threading.Thread(target=lambda: embed.embed_query("warm up"), daemon=True).start()
    if not http:
        build(config.resolve_project(project)).run()
        return
    mcp = build(config.resolve_project(project) if project else None, per_session=True)
    idle = float(os.environ.get("SESSION_RAG_IDLE_UNLOAD", "900"))
    if idle > 0:
        threading.Thread(target=_idle_watch, args=(mcp.projects, idle), daemon=True).start()
    mcp.run("streamable-http", host=host, port=port, json_response=True)
