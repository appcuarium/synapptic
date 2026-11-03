"""Tests for synapptic.config module constants and path definitions.

The profile tuning constants, the dimension routing sets and the filter signal lists left this module for
synapptic-core (2026-11-03); what stays public is paths, the dimension names and the filtering defaults.
"""

from pathlib import Path

from synapptic.config import (
    CHARS_PER_TOKEN,
    CLAUDE_PROJECTS_DIR,
    DEFAULT_MAX_TOKENS,
    DIMENSIONS,
    SYNAPPTIC_DIR,
)


class TestPaths:

    def test_synapptic_dir_under_home(self):
        home = Path.home()
        assert SYNAPPTIC_DIR.is_relative_to(home)

    def test_claude_projects_dir_under_home(self):
        home = Path.home()
        assert CLAUDE_PROJECTS_DIR.is_relative_to(home)


class TestDimensions:

    def test_all_dimensions_defined(self):
        assert len(DIMENSIONS) == 9
        assert "guards" in DIMENSIONS
        assert "ai_failures" in DIMENSIONS
        assert "workflow" in DIMENSIONS


class TestDefaults:

    def test_chars_per_token_positive(self):
        assert CHARS_PER_TOKEN > 0

    def test_default_max_tokens_positive(self):
        assert DEFAULT_MAX_TOKENS > 0
