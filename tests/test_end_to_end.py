from session_rag.db import connect
from session_rag.index import run
from session_rag.search import context, search


def test_discovery(env):
    ps = env["config"].projects()
    assert "shop" in ps
    assert env["config"].resolve_project(str(env["project_dir"] / "sub")) == "shop"


def test_index_search_and_redaction(env):
    stats = run(log=lambda *_: None)
    assert stats["shop"]["added"] >= 5
    con = connect(readonly=True)
    texts = [r[0] for r in con.execute("SELECT text FROM chunks")]
    assert not any("hunter2hunter" in t for t in texts)

    hits = search(con, "10.9.8.7", k=3, projects=["shop"])
    assert hits and "10.9.8.7" in hits[0]["text"]

    hits = search(con, "node-selector", k=3, projects=["shop"], roles=["doc"])
    assert hits and hits[0]["role"] == "doc"

    ctx = context(con, hits[0]["id"], 2, 2)
    assert any(r["current"] for r in ctx)


def test_incremental(env):
    run(log=lambda *_: None)
    again = run(log=lambda *_: None)
    assert again["shop"]["added"] == 0
    with open(env["transcript"], "a") as fh:
        fh.write('{"type":"user","sessionId":"s1","uuid":"u5","timestamp":"2026-10-02T00:00:00Z",'
                 '"message":{"content":"a brand new follow-up question about invoices"}}\n')  # fmt: skip
    third = run(log=lambda *_: None)
    assert third["shop"]["added"] == 1
