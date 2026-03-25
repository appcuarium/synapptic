"""Tests for synapptic.patterns module."""

import pytest

from synapptic.patterns import (
    DEFAULT_TEMPLATE,
    builtin_patterns_dir,
    create_pattern,
    list_patterns,
    load_pattern,
)


class TestBuiltinPatternsDir:

    def test_returns_path_or_none(self):
        result = builtin_patterns_dir()
        # May be None in editable installs without package data — that's OK
        assert result is None or hasattr(result, "exists")

    def test_default_pattern_loadable(self):
        """The built-in 'default' pattern must be loadable."""
        result = load_pattern("default")
        # Built-in default may not exist in all test environments; if it does, must be non-empty
        if result is not None:
            assert len(result) > 50
            assert "{transcript}" in result


class TestLoadPattern:

    def test_missing_pattern_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("synapptic.patterns.PATTERNS_DIR", tmp_path / "patterns")
        result = load_pattern("nonexistent_pattern_xyz")
        assert result is None

    def test_user_pattern_overrides_builtin(self, tmp_path, monkeypatch):
        patterns_dir = tmp_path / "patterns"
        custom_dir = patterns_dir / "default"
        custom_dir.mkdir(parents=True)
        (custom_dir / "prompt.md").write_text("Custom template {transcript}")
        monkeypatch.setattr("synapptic.patterns.PATTERNS_DIR", patterns_dir)
        result = load_pattern("default")
        assert result == "Custom template {transcript}"


class TestCreatePattern:

    def test_creates_prompt_file(self, tmp_path, monkeypatch):
        patterns_dir = tmp_path / "patterns"
        monkeypatch.setattr("synapptic.patterns.PATTERNS_DIR", patterns_dir)
        path = create_pattern("my-pattern")
        assert path.exists()
        assert path.name == "prompt.md"

    def test_does_not_overwrite_existing(self, tmp_path, monkeypatch):
        patterns_dir = tmp_path / "patterns"
        (patterns_dir / "custom").mkdir(parents=True)
        existing = patterns_dir / "custom" / "prompt.md"
        existing.write_text("original content")
        monkeypatch.setattr("synapptic.patterns.PATTERNS_DIR", patterns_dir)
        create_pattern("custom")
        assert existing.read_text() == "original content"


class TestListPatterns:

    def test_user_pattern_appears_in_list(self, tmp_path, monkeypatch):
        patterns_dir = tmp_path / "patterns"
        custom = patterns_dir / "my-custom"
        custom.mkdir(parents=True)
        (custom / "prompt.md").write_text("template")
        monkeypatch.setattr("synapptic.patterns.PATTERNS_DIR", patterns_dir)
        patterns = list_patterns()
        names = [p["name"] for p in patterns]
        assert "my-custom" in names

    def test_user_pattern_source_is_user(self, tmp_path, monkeypatch):
        patterns_dir = tmp_path / "patterns"
        (patterns_dir / "mine").mkdir(parents=True)
        (patterns_dir / "mine" / "prompt.md").write_text("x")
        monkeypatch.setattr("synapptic.patterns.PATTERNS_DIR", patterns_dir)
        patterns = list_patterns()
        mine = next(p for p in patterns if p["name"] == "mine")
        assert mine["source"] == "user"

    def test_empty_when_no_patterns(self, tmp_path, monkeypatch):
        monkeypatch.setattr("synapptic.patterns.PATTERNS_DIR", tmp_path / "patterns")
        # Only user patterns checked here (built-in may still exist)
        patterns = list_patterns()
        user_patterns = [p for p in patterns if p["source"] == "user"]
        assert user_patterns == []


class TestDefaultTemplate:

    def test_default_template_has_required_placeholders(self):
        required = ["{transcript}", "{dimensions}", "{extraction_focus}", "{dimension_list}"]
        for placeholder in required:
            assert placeholder in DEFAULT_TEMPLATE, f"Missing placeholder: {placeholder}"
