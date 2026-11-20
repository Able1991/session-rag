import importlib


def test_build_query_and_export(env, monkeypatch, tmp_path):
    from session_rag import index

    index.run(None, log=lambda *_: None)
    import session_rag.digest as digest
    import session_rag.knowledge as knowledge

    importlib.reload(knowledge)
    from session_rag.db import connect

    con = connect(readonly=True)
    ids = [r[0] for r in con.execute("SELECT id FROM chunks WHERE project = 'shop' AND session = 's1' ORDER BY id")]
    con.close()

    def fake(text, prompt=digest.PROMPT):
        assert f"[#{ids[-1]} " in text
        return (
            '```json\n{"entities": [{"name": "Object storage", "kind": "service", "aliases": ["S3", "bucket"]},'
            '{"name": "egress IP", "kind": "ip"}],'
            f'"facts": [{{"entity": "S3", "fact": "Uploads time out because the provider blocks egress IP 10.9.8.7.",'
            f' "ref": {ids[-1]}}}, {{"entity": "egress IP", "fact": "Our egress IP is 10.9.8.7.", "ref": "#999999"}}],'
            '"relations": [{"a": "Object storage", "b": "egress IP", "label": "blocks"}]}\n```'
        )

    monkeypatch.setattr(knowledge, "_call_claude", fake)
    monkeypatch.setattr(knowledge, "MIN_SEGMENT", 0)
    monkeypatch.setattr(knowledge.time, "strftime", lambda f, *a: "2099-01-01" if f == "%Y-%m-%d" else "x")
    assert knowledge.build("shop", log=lambda *_: None) == 1
    assert knowledge.build("shop", log=lambda *_: None) == 0  # already done

    con = connect(readonly=True)
    e = knowledge.entity(con, "bucket", ["shop"])  # alias
    assert e["name"] == "Object storage" and len(e["facts"]) == 1
    assert e["facts"][0]["chunk"] == ids[-1]
    assert e["related"][0]["entity"] == "egress IP"
    ip = knowledge.entity(con, "egress ip", ["shop"])
    assert ip["facts"][0]["chunk"] is None  # ref outside the segment is dropped

    from session_rag.search import search

    res = search(con, "why do uploads time out", projects=["shop"])
    hit = next(r for r in res if r["id"] == ids[-1])  # the evidence chunk, annotated with its fact
    assert hit["text"].startswith("[fact] Object storage:") and res.index(hit) < 3
    con.close()

    assert knowledge.export("shop", tmp_path / "vault") == 2
    page = (tmp_path / "vault" / "Object storage.md").read_text()
    assert "[[egress IP]]" in page and f"#{ids[-1]}" in page
