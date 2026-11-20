from session_rag.parse import MAX_CHUNK, parse_markdown, parse_transcript, split


def test_transcript_roles(env):
    chunks, offset = parse_transcript(env["transcript"])
    roles = [c.role for c in chunks]
    assert roles == ["user", "tool", "result", "assistant"]
    assert offset == env["transcript"].stat().st_size
    assert "Probe S3" in chunks[1].text and "curl -m 5" in chunks[1].text


def test_offset_resumes(env):
    _, offset = parse_transcript(env["transcript"])
    more, new = parse_transcript(env["transcript"], offset)
    assert more == [] and new == offset


def test_partial_line_is_not_consumed(tmp_path):
    f = tmp_path / "t.jsonl"
    f.write_text('{"type":"user","message":{"content":"a complete first message here"}}\n{"type":"user"')
    chunks, offset = parse_transcript(f)
    assert len(chunks) == 1
    assert offset == f.read_text().index("\n") + 1


def test_split_overlap():
    parts = split("x" * (MAX_CHUNK * 3))
    assert all(len(p) <= MAX_CHUNK for p in parts) and len(parts) >= 3


def test_markdown_sections(env):
    chunks = parse_markdown(env["project_dir"] / "CLAUDE.md")
    assert any("make release" in c.text for c in chunks)
    assert all(c.role == "doc" for c in chunks)
