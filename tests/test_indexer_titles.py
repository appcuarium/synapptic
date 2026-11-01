"""Session titles in the index come from the transcript's title records, not from the first message."""

import json

from synapptic.sessions.indexer import extract_session_metadata

FIRST_MESSAGE = "<system-reminder>injected text</system-reminder>\nanalyze the profile of the trades in strategy 165"


def write_transcript(path, *title_records):
    records = [
        {"type": "user", "timestamp": "2026-10-31T17:21:00Z", "message": {"role": "user", "content": FIRST_MESSAGE}},
        *title_records,
        {"type": "assistant", "timestamp": "2026-10-31T17:22:00Z",
         "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "ok"}]}},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    return path


def test_the_users_name_wins_then_claude_codes_title_then_the_first_message(tmp_path):
    named = write_transcript(tmp_path / "a.jsonl",
                             {"type": "ai-title", "aiTitle": "Analyze strategy 165 trade profile"},
                             {"type": "custom-title", "customTitle": "Strategy Parametrization"})
    titled = write_transcript(tmp_path / "b.jsonl",
                              {"type": "ai-title", "aiTitle": "Old title"},
                              {"type": "ai-title", "aiTitle": "Analyze strategy 165 trade profile"})
    untitled = write_transcript(tmp_path / "c.jsonl")
    assert extract_session_metadata(named)["title"] == "Strategy Parametrization"
    assert extract_session_metadata(titled)["title"] == "Analyze strategy 165 trade profile"   # the latest one
    assert extract_session_metadata(untitled)["title"] == "<system-reminder>injected text</system-reminder>"
    assert extract_session_metadata(named)["message_count"] == 2   # title records are not messages
