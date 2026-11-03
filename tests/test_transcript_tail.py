"""Reading a growing transcript incrementally gives the same conversation as reading it whole, and reports which
turn changed; messages typed while the agent works are user turns."""

import json

from synapptic.sessions.parse import parse_session_full
from synapptic.sessions.tail import TranscriptTail


def user(text, ts):
    return {"type": "user", "timestamp": ts, "message": {"role": "user", "content": text}}


def assistant(message_id, block, ts):
    return {"type": "assistant", "timestamp": ts,
            "message": {"id": message_id, "role": "assistant", "model": "claude-opus-5-5", "content": [block],
                        "usage": {"input_tokens": 1, "output_tokens": 2}}}


def tool_result(ts):
    return {"type": "user", "timestamp": ts,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}}


def queued(text, ts):
    """How Claude Code stores a message the user sent while the agent was working."""
    return {"type": "attachment", "timestamp": ts,
            "attachment": {"type": "queued_command", "prompt": text, "commandMode": "prompt",
                           "origin": {"kind": "human"}, "timestamp": ts, "humanTurn": True}}


RECORDS = [
    user("how do I start the relay?", "2026-11-01T10:00:00Z"),
    {"type": "ai-title", "aiTitle": "Start the relay"},
    assistant("msg_A", {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls"}}, "2026-11-01T10:00:01Z"),
    tool_result("2026-11-01T10:00:02Z"),
    queued("also check the port", "2026-11-01T10:00:03Z"),
    assistant("msg_B", {"type": "text", "text": "It runs on 5100."}, "2026-11-01T10:00:04Z"),
]


def lines(records):
    return [(json.dumps(record) + "\n").encode() for record in records]


def test_reading_in_pieces_gives_the_whole_files_conversation(tmp_path):
    data = b"".join(lines(RECORDS))
    whole = tmp_path / "whole.jsonl"
    whole.write_bytes(data)
    grown = tmp_path / "grown.jsonl"
    grown.write_bytes(b"")
    tail = TranscriptTail(grown, scrub=False)
    for cut in (37, 140, 141, 400, len(data) - 5, len(data)):   # several cuts land in the middle of a line
        with open(grown, "ab") as f:
            f.write(data[grown.stat().st_size:cut])
        tail.update()
    assert tail.turns == parse_session_full(whole, scrub=False)
    # user + assistant records: you, the tool call, its result, the reply (the mid-turn message is an attachment)
    assert tail.message_count == 4 and tail.title == "Start the relay"
    assert tail.last_timestamp == "2026-11-01T10:00:04Z"


def test_a_message_sent_mid_turn_is_a_user_turn(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_bytes(b"".join(lines(RECORDS)))
    tail = TranscriptTail(path, scrub=False)
    tail.update()
    assert [turn["role"] for turn in tail.turns] == ["user", "assistant", "user", "assistant"]
    assert tail.turns[2]["blocks"] == [{"type": "text", "text": "also check the port"}]


def test_update_reports_the_first_turn_that_changed(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_bytes(b"".join(lines(RECORDS[:3])))   # you, then the agent's tool call
    tail = TranscriptTail(path, scrub=False)
    assert tail.update() == 0
    assert tail.update() is None                     # nothing appended
    with open(path, "ab") as f:
        f.write(b"".join(lines([tool_result("2026-11-01T10:00:02Z")])))
    assert tail.update() == 1                        # the tool output joined the agent's turn
    with open(path, "ab") as f:
        f.write(b"".join(lines([user("thanks", "2026-11-01T10:00:09Z")])))
    assert tail.update() == 2 and len(tail.turns) == 3


def test_a_truncated_transcript_is_read_again_from_the_start(tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_bytes(b"".join(lines(RECORDS)))
    tail = TranscriptTail(path, scrub=False)
    tail.update()
    path.write_bytes(b"".join(lines(RECORDS[:1])))
    assert tail.update() == 0
    assert len(tail.turns) == 1 and tail.message_count == 1
