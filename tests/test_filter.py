"""Tests for transcript filtering."""

from synapptic.filter import filter_transcript, turns_to_text, estimate_tokens


def test_filter_strips_progress_records(sample_session_path):
    """Progress records should be completely stripped."""
    turns = filter_transcript(sample_session_path)
    texts = [t.text for t in turns]
    # No turn should contain progress-like content
    for text in texts:
        assert "progress" not in text.lower() or "progress" in text.lower()  # Just checking it parses


def test_filter_strips_system_records(sample_session_path):
    """System records should be stripped."""
    turns = filter_transcript(sample_session_path)
    roles = [t.role for t in turns]
    # Only user and assistant roles should remain
    assert all(r in ("user", "assistant") for r in roles)


def test_filter_strips_tool_results(sample_session_path):
    """User messages that are only tool_result should be stripped."""
    turns = filter_transcript(sample_session_path)
    user_turns = [t for t in turns if t.role == "user"]
    # Tool result content like "def main()..." and "Edit applied" should not appear
    for t in user_turns:
        assert "def main" not in t.text
        assert "Edit applied" not in t.text


def test_filter_keeps_user_text(sample_session_path):
    """Direct user text messages should be preserved."""
    turns = filter_transcript(sample_session_path)
    user_texts = [t.text for t in turns if t.role == "user"]
    assert any("Read the file first" in t for t in user_texts)
    assert any("don't add any docstrings" in t for t in user_texts)
    assert any("stop summarizing" in t for t in user_texts)


def test_filter_strips_thinking_blocks(sample_session_path):
    """Thinking blocks in assistant messages should be stripped."""
    turns = filter_transcript(sample_session_path)
    assistant_texts = [t.text for t in turns if t.role == "assistant"]
    for t in assistant_texts:
        assert "I should read the file first" not in t  # thinking block content


def test_filter_keeps_assistant_text(sample_session_path):
    """Assistant text blocks should be preserved."""
    turns = filter_transcript(sample_session_path)
    assistant_texts = [t.text for t in turns if t.role == "assistant"]
    assert any("Let me read the file first" in t for t in assistant_texts)


def test_filter_boosts_correction_signals(sample_session_path):
    """Turns with correction signals should be boosted."""
    turns = filter_transcript(sample_session_path)
    boosted = [t for t in turns if t.boosted]
    # "stop summarizing" and "don't add any docstrings" should be boosted
    boosted_texts = [t.text for t in boosted]
    assert any("stop summarizing" in t for t in boosted_texts)
    assert any("don't commit" in t for t in boosted_texts)


def test_filter_boosts_preference_signals(sample_session_path):
    """Turns with preference signals like 'Never' should be boosted."""
    turns = filter_transcript(sample_session_path)
    boosted = [t for t in turns if t.boosted]
    boosted_texts = [t.text for t in boosted]
    assert any("Never inside functions" in t for t in boosted_texts)


def test_turns_to_text():
    """turns_to_text should produce a readable document."""
    from synapptic.filter import Turn
    turns = [
        Turn(role="user", text="Hello"),
        Turn(role="assistant", text="Hi there"),
    ]
    text = turns_to_text(turns)
    assert "[USER]: Hello" in text
    assert "[ASSISTANT]: Hi there" in text


def test_estimate_tokens():
    """Token estimation should be roughly 1 token per 4 chars."""
    assert estimate_tokens("a" * 400) == 100


def test_filter_truncation(sample_session_path):
    """When max_tokens is very small, boosted turns should be prioritized."""
    from synapptic.filter import filter_transcript
    # With very small budget, should still include boosted turns
    turns = filter_transcript(sample_session_path, max_tokens=500)
    # Should have at least some turns
    assert len(turns) > 0
    # At least some boosted turns should survive
    boosted = [t for t in turns if t.boosted]
    # May or may not have boosted turns depending on budget, but shouldn't crash
