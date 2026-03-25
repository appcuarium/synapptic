"""Basic smoke tests for CLI entry points."""

import pytest
from click.testing import CliRunner

from synapptic.cli import cli


@pytest.fixture
def runner():
    return CliRunner()


class TestCliHelp:

    def test_help(self, runner):
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "ingest" in result.output

    def test_version(self, runner):
        result = runner.invoke(cli, ["--version"])
        assert result.exit_code == 0

    def test_config_help(self, runner):
        result = runner.invoke(cli, ["config", "--help"])
        assert result.exit_code == 0
        assert "show" in result.output

    def test_relay_help(self, runner):
        result = runner.invoke(cli, ["relay", "--help"])
        assert result.exit_code == 0


class TestConfigShow:

    def test_config_show_runs(self, runner, tmp_path, monkeypatch):
        """config show must not crash on a fresh install with no config file."""
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0

    def test_config_show_with_provider(self, runner, tmp_path, monkeypatch):
        """config show reflects the saved provider."""
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", config_path)
        from synapptic.providers import save_config
        save_config({"provider": "ollama", "model": "llama3", "api_url": "http://localhost:11434/v1"})
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "ollama" in result.output.lower()


class TestStats:

    def test_stats_no_data(self, runner, tmp_path, monkeypatch):
        """stats must not crash when there is no session data."""
        monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", tmp_path)
        monkeypatch.setattr("synapptic.state.PROJECTS_DIR", tmp_path / "projects")
        monkeypatch.setattr("synapptic.state.GLOBAL_DIR", tmp_path / "global")
        monkeypatch.setattr("synapptic.state.CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
        result = runner.invoke(cli, ["stats"])
        assert result.exit_code == 0


class TestArchetypeCommand:

    def test_archetype_no_data(self, runner, tmp_path, monkeypatch):
        """archetype command exits cleanly when no archetype has been generated."""
        monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", tmp_path)
        monkeypatch.setattr("synapptic.state.GLOBAL_DIR", tmp_path / "global")
        monkeypatch.setattr("synapptic.state.PROJECTS_DIR", tmp_path / "projects")
        result = runner.invoke(cli, ["archetype"])
        assert result.exit_code == 0


class TestProfileCommand:

    def test_profile_no_data(self, runner, tmp_path, monkeypatch):
        """profile command exits cleanly when profile is empty."""
        monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", tmp_path)
        monkeypatch.setattr("synapptic.state.GLOBAL_DIR", tmp_path / "global")
        monkeypatch.setattr("synapptic.state.PROJECTS_DIR", tmp_path / "projects")
        result = runner.invoke(cli, ["profile"])
        assert result.exit_code == 0
