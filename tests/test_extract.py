"""Tests for extract.py — JSON extraction, prompt building, dimension validation."""

import json
import sys
from io import StringIO
from unittest.mock import patch

from synapptic.extract import (
    build_extraction_prompt,
    extract_json,
    extract_observations,
    get_active_dimensions,
)


class TestExtractJson:

    def test_clean_array(self):
        assert extract_json('[{"a": 1}]') == '[{"a": 1}]'

    def test_markdown_fences(self):
        raw = '```json\n[{"a": 1}]\n```'
        assert extract_json(raw) == '[{"a": 1}]'

    def test_trailing_text(self):
        raw = 'Here is the result:\n[{"a": 1}]\nDone.'
        assert extract_json(raw) == '[{"a": 1}]'

    def test_no_boundaries_warns(self, capsys):
        result = extract_json("no json here at all")
        captured = capsys.readouterr()
        assert "no JSON array boundaries" in captured.err
        assert result == "no json here at all"

    def test_only_open_bracket(self, capsys):
        result = extract_json("starts with [ but never closes")
        captured = capsys.readouterr()
        assert "no JSON array boundaries" in captured.err

    def test_empty_array(self):
        assert extract_json("[]") == "[]"

    def test_nested_arrays(self):
        raw = '[[1, 2], [3, 4]]'
        assert extract_json(raw) == '[[1, 2], [3, 4]]'


class TestGetActiveDimensions:

    def test_default_mode_returns_all(self):
        dims = get_active_dimensions({})
        assert "guards" in dims
        assert "workflow" in dims

    def test_user_mode_excludes_agent_dims(self):
        dims = get_active_dimensions({"profiling_mode": "user"})
        assert "workflow" in dims
        assert "guards" not in dims
        assert "ai_failures" not in dims

    def test_agent_mode_excludes_user_dims(self):
        dims = get_active_dimensions({"profiling_mode": "agent"})
        assert "guards" in dims
        assert "ai_failures" in dims
        assert "workflow" not in dims
        assert "code_style" not in dims


class TestBuildExtractionPrompt:

    def test_includes_dimensions(self):
        from synapptic.filter import Turn
        turns = [Turn(role="user", text="hello", timestamp="2026-01-01")]
        prompt = build_extraction_prompt(turns)
        assert "dimension" in prompt.lower()

    def test_includes_profile_summary(self):
        from synapptic.filter import Turn
        turns = [Turn(role="user", text="hello", timestamp="2026-01-01")]
        profile = {
            "dimensions": {
                "workflow": [{"observation": "reads files first", "weight": 0.9}],
            },
        }
        prompt = build_extraction_prompt(turns, profile=profile)
        assert "reads files first" in prompt


class TestExtractObservationsValidation:

    def test_invalid_dimension_warns(self, capsys):
        from synapptic.filter import Turn
        turns = [Turn(role="user", text="test", timestamp="2026-01-01")]
        obs = [
            {"dimension": "guard", "observation": "bad dim", "confidence": 0.9},
            {"dimension": "guards", "observation": "good dim", "confidence": 0.9},
        ]
        with patch("synapptic.extract.call_llm", return_value=json.dumps(obs)):
            result = extract_observations(turns, "test-session", config={"provider": "test"})
        captured = capsys.readouterr()
        assert "invalid dimension" in captured.err
        assert "did you mean 'guards'" in captured.err
        assert len(result) == 1
        assert result[0]["dimension"] == "guards"

    def test_adds_session_metadata(self):
        from synapptic.filter import Turn
        turns = [Turn(role="user", text="test", timestamp="2026-01-01")]
        obs = [{"dimension": "guards", "observation": "test obs", "confidence": 0.8}]
        with patch("synapptic.extract.call_llm", return_value=json.dumps(obs)):
            result = extract_observations(turns, "sess-123", config={"provider": "test"})
        assert result[0]["session_id"] == "sess-123"
        assert "timestamp" in result[0]

    def test_llm_failure_returns_empty(self):
        from synapptic.filter import Turn
        turns = [Turn(role="user", text="test", timestamp="2026-01-01")]
        with patch("synapptic.extract.call_llm", return_value=None):
            result = extract_observations(turns, "test", config={"provider": "test"})
        assert result == []
