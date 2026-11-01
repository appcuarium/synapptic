"""The index counts a session's turns as the dashboard's conversation shows them, not its raw records."""

import json

from synapptic.sessions.indexer import extract_session_metadata

RECORDS = [
    {"type": "user", "timestamp": "2026-10-31T17:21:00Z", "message": {"role": "user", "content": "how do I start the relay?"}},
    # one agent turn: thinking, a tool call, its result, then text (4 records)
    {"type": "assistant", "timestamp": "2026-10-31T17:22:00Z",
     "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "look first"}]}},
    {"type": "assistant", "timestamp": "2026-10-31T17:22:01Z",
     "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}]}},
    {"type": "user", "timestamp": "2026-10-31T17:22:02Z",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "app.py"}]}},
    {"type": "assistant", "timestamp": "2026-10-31T17:22:03Z",
     "message": {"role": "assistant", "content": [{"type": "text", "text": "Run synapptic relay start."}]}},
    {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-31T17:24:00Z",
     "compactMetadata": {"trigger": "auto", "preTokens": 1000}},
    {"type": "user", "timestamp": "2026-10-31T17:25:00Z", "message": {"role": "user", "content": "it errors"}},
    # an agent turn with only thinking shows nothing in the conversation: not a turn
    {"type": "assistant", "timestamp": "2026-10-31T17:25:05Z",
     "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "hmm"}]}},
]


def test_turns_are_what_the_conversation_shows(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in RECORDS) + "\n")
    meta = extract_session_metadata(path)
    assert meta["message_count"] == 7     # every user/assistant record
    assert meta["turn_count"] == 4        # you, the agent, the compaction marker, you
