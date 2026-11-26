"""The MCP tools declare all four annotation hints (MCP directories reject tools without them)."""

import anyio

from session_rag.server import build


def test_tools_are_annotated_read_only():
    tools = anyio.run(build(None).list_tools)
    assert {t.name for t in tools} == {"search_history", "get_context", "knowledge"}
    for t in tools:
        wire = t.model_dump(by_alias=True, exclude_none=True)["annotations"]
        assert wire == {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}
