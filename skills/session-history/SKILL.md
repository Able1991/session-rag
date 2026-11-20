---
name: session-history
description: Use when the user asks about earlier work in this or another project ("how did we fix…", "what was the IP/path/command for…", "what did we decide about…", "what did support answer…"), when continuing a task from a previous session, or after context compaction when exact details are missing. Searches past Claude Code sessions with the session-rag MCP tools before grepping transcripts.
---

# Searching past sessions

The `history` MCP server (session-rag) indexes every past Claude Code session and the project's docs
locally. Use it before reconstructing facts from scratch or grepping `~/.claude/projects/**/*.jsonl`.

1. Call `search_history` with the concrete thing you need: an error string, IP, pod or file name, or a
   short natural-language question, and pass 2-3 alternative phrasings in `also`: the other language,
   synonyms, likely config keys, variable or command names. Retrieval is mostly lexical, so phrasings
   that share words with how the answer was written matter more than anything else.
   - `roles="assistant"` returns conclusions and summaries; `roles="tool"` returns commands that were run;
     `roles="result"` returns their output; `roles="doc"` returns project docs and memory.
   - `since="YYYY-MM-DD"` narrows to recent work; `project="all"` searches every project.
2. Call `get_context(id)` on the best hit to read the surrounding messages before relying on it.
3. Treat results as history, not truth: check the current state (files, cluster, config) before acting.
   Secrets appear as `***`; fetch real values from the secret store, never from history.
4. Fall back to grep over transcripts only when `search_history` finds nothing relevant.
