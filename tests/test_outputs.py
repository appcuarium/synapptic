"""Tests for synapptic.outputs — per-tool archetype writers."""

from pathlib import Path

from synapptic.outputs import (
    resolve_target_model,
    write_aider,
    write_claude_code,
    write_cline,
    write_codex,
    write_continue,
    write_copilot,
    write_cursor,
    write_gemini,
    write_windsurf,
)


class TestWriteClaudeCode:

    def test_creates_archetype_with_frontmatter(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        write_claude_code("## Archetype\nContent", memory_dir)
        path = memory_dir / "user_archetype.md"
        assert path.exists()
        content = path.read_text()
        assert "---" in content
        assert "type: user" in content
        assert "## Archetype" in content

    def test_updates_memory_md(self, tmp_path):
        memory_dir = tmp_path / "memory"
        memory_dir.mkdir()
        (memory_dir / "MEMORY.md").write_text("# Project Memory\n\nSome content.\n")
        write_claude_code("archetype text", memory_dir)
        content = (memory_dir / "MEMORY.md").read_text()
        assert "user_archetype.md" in content


class TestWriteCursor:

    def test_creates_mdc_file(self, tmp_path):
        write_cursor("## Archetype", tmp_path)
        path = tmp_path / ".cursor" / "rules" / "synapptic.mdc"
        assert path.exists()
        content = path.read_text()
        assert "alwaysApply: true" in content
        assert "## Archetype" in content


class TestWriteCopilot:

    def test_creates_with_markers(self, tmp_path):
        write_copilot("archetype text", tmp_path)
        path = tmp_path / ".github" / "copilot-instructions.md"
        assert path.exists()
        content = path.read_text()
        assert "<!-- synapptic:start -->" in content
        assert "archetype text" in content
        assert "<!-- synapptic:end -->" in content

    def test_replaces_existing_section(self, tmp_path):
        write_copilot("first version", tmp_path)
        write_copilot("second version", tmp_path)
        content = (tmp_path / ".github" / "copilot-instructions.md").read_text()
        assert "second version" in content
        assert "first version" not in content

    def test_appends_to_existing_content(self, tmp_path):
        github_dir = tmp_path / ".github"
        github_dir.mkdir()
        (github_dir / "copilot-instructions.md").write_text("# Existing rules\n\nDo X always.\n")
        write_copilot("synapptic content", tmp_path)
        content = (github_dir / "copilot-instructions.md").read_text()
        assert "Existing rules" in content
        assert "synapptic content" in content


class TestWriteGemini:

    def test_creates_styleguide(self, tmp_path):
        write_gemini("archetype text", tmp_path)
        path = tmp_path / ".gemini" / "styleguide.md"
        assert path.exists()
        assert "archetype text" in path.read_text()


class TestWriteCodex:

    def test_creates_agents_md(self, tmp_path):
        write_codex("archetype text", tmp_path)
        path = tmp_path / "AGENTS.md"
        assert path.exists()
        content = path.read_text()
        assert "<!-- synapptic:start -->" in content
        assert "archetype text" in content


class TestSimpleWriters:

    def test_windsurf(self, tmp_path):
        write_windsurf("content", tmp_path)
        assert (tmp_path / ".windsurfrules").read_text().strip() == "content"

    def test_cline(self, tmp_path):
        write_cline("content", tmp_path)
        assert (tmp_path / ".clinerules").read_text().strip() == "content"

    def test_aider(self, tmp_path):
        write_aider("content", tmp_path)
        assert (tmp_path / "CONVENTIONS.md").read_text().strip() == "content"

    def test_continue(self, tmp_path):
        write_continue("content", tmp_path)
        assert (tmp_path / ".continuerules").read_text().strip() == "content"


class TestResolveTargetModel:

    def test_finds_best_model_for_family(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "G1", "model_verdicts": {"claude-sonnet-4-6": "effective", "gemini-2.0-flash": "redundant"}},
                    {"observation": "G2", "model_verdicts": {"claude-sonnet-4-6": "effective"}},
                ],
            },
        }
        result = resolve_target_model("claude-code", profile)
        assert result == "claude-sonnet-4-6"

    def test_no_matching_family_returns_none(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "G1", "model_verdicts": {"llama-3.1": "effective"}},
                ],
            },
        }
        result = resolve_target_model("claude-code", profile)
        assert result is None

    def test_no_verdicts_returns_none(self):
        profile = {"dimensions": {"guards": [{"observation": "G1"}]}}
        assert resolve_target_model("claude-code", profile) is None

    def test_cursor_has_no_family(self):
        profile = {"dimensions": {"guards": [{"observation": "G1", "model_verdicts": {"claude": "effective"}}]}}
        assert resolve_target_model("cursor", profile) is None
