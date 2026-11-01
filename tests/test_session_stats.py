"""Session token stats count every API response once, and price it at the published rates.

Claude Code writes one API response as several transcript records (one per text / tool call / thinking block),
each carrying the response's full usage. Rates: platform.claude.com/docs/en/about-claude/pricing (2026-11-01).
"""

import json

import pytest

from synapptic.sessions.parse import compute_session_stats, parse_session_full


def assistant(message_id, model, usage, block):
    return {"type": "assistant", "timestamp": "2026-11-01T10:00:00Z",
            "message": {"id": message_id, "role": "assistant", "model": model, "usage": usage, "content": [block]}}


def write(tmp_path, records):
    path = tmp_path / "s.jsonl"
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n")
    return path


def test_each_api_response_is_counted_once(tmp_path):
    usage = {"input_tokens": 10, "output_tokens": 100, "cache_read_input_tokens": 1000, "cache_creation_input_tokens": 50}
    records = [
        {"type": "user", "timestamp": "2026-11-01T10:00:00Z", "message": {"role": "user", "content": "go"}},
        # one response written as three records, each with the same full usage
        assistant("msg_A", "claude-opus-5-5", usage, {"type": "thinking", "thinking": "plan"}),
        assistant("msg_A", "claude-opus-5-5", usage, {"type": "text", "text": "looking"}),
        assistant("msg_A", "claude-opus-5-5", usage, {"type": "tool_use", "id": "t1", "name": "Bash", "input": {}}),
        {"type": "user", "timestamp": "2026-11-01T10:00:01Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok"}]}},
        assistant("msg_B", "claude-opus-5-5", usage, {"type": "text", "text": "done"}),
    ]
    turns = parse_session_full(write(tmp_path, records), scrub=False)
    stats = compute_session_stats(turns)
    assert stats["total_requests"] == 2
    assert stats["total_output_tokens"] == 200 and stats["total_cache_read_tokens"] == 2000
    assert turns[1]["usage"]["output_tokens"] == 200   # the turn's badge: two responses, not six records


def test_cost_uses_each_models_published_rates(tmp_path):
    million = 1_000_000
    records = [
        {"type": "user", "timestamp": "2026-11-01T10:00:00Z", "message": {"role": "user", "content": "go"}},
        # Opus 5.5: $4 in, $20 out, $0.20 cache read, $8 1h cache write
        assistant("msg_A", "claude-opus-5-5",
                  {"input_tokens": million, "output_tokens": million, "cache_read_input_tokens": million,
                   "cache_creation_input_tokens": million,
                   "cache_creation": {"ephemeral_1h_input_tokens": million, "ephemeral_5m_input_tokens": 0}},
                  {"type": "text", "text": "a"}),
        {"type": "user", "timestamp": "2026-11-01T10:00:01Z", "message": {"role": "user", "content": "more"}},
        # Haiku 4.5 by its dated id: $1 in, $5 out, $1.25 5m cache write (no split given: 5-minute)
        assistant("msg_B", "claude-haiku-4-5-20251001",
                  {"input_tokens": million, "output_tokens": million, "cache_creation_input_tokens": million},
                  {"type": "text", "text": "b"}),
        {"type": "user", "timestamp": "2026-11-01T10:00:02Z", "message": {"role": "user", "content": "long"}},
        # Haiku 5.5 with a prompt over 100,000 tokens: the long-prompt rates, $0.50 in and $2.50 out
        assistant("msg_C", "claude-haiku-5-5", {"input_tokens": 200_000, "output_tokens": million},
                  {"type": "text", "text": "c"}),
    ]
    stats = compute_session_stats(parse_session_full(write(tmp_path, records), scrub=False))
    opus = 4 + 20 + 0.20 + 8
    haiku_45 = 1 + 5 + 1.25
    haiku_55_long = 0.2 * 0.50 + 2.50
    assert stats["estimated_cost_usd"] == pytest.approx(opus + haiku_45 + haiku_55_long, abs=1e-4)
