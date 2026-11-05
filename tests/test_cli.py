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


class TestConfigProvider:
    """`synapptic config provider` on the per-provider entries (operator 2026-11-06): the default preselected
    and marked, a provider configured beside it asked "Set default?", its settings kept in its own entry.
    The connection check at the end goes to a closed local port, so it fails at once and the command ends."""

    UNREACHABLE = "http://127.0.0.1:9/v1"   # boundary: the provider's server; a closed port fails the check at once

    def test_a_second_provider_is_kept_beside_the_default_unless_made_so(self, runner, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", config_path)
        from synapptic.providers import load_config, save_config
        save_config({"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-key"})
        # Ollama is 4; its first model; the URL; "Set default?" answered no
        result = runner.invoke(cli, ["config", "provider"], input=f"4\n1\n{self.UNREACHABLE}\nn\n")
        assert result.exit_code == 0, result.output
        assert "Anthropic API (default)" in result.output and "Set default?" in result.output
        saved = load_config()
        assert saved["provider"] == "anthropic" and saved["model"] == "claude-sonnet-5-5" and saved["api_key"] == "sk-ant-key"
        assert saved["providers"]["ollama"]["api_url"] == self.UNREACHABLE and saved["providers"]["ollama"]["model"]
        # run again: Ollama shows as configured, and this time it is made the default
        result = runner.invoke(cli, ["config", "provider"], input=f"4\n1\n{self.UNREACHABLE}\ny\n")
        assert result.exit_code == 0, result.output
        assert "Ollama (local) (configured)" in result.output
        saved = load_config()
        assert saved["provider"] == "ollama" and saved["api_url"] == self.UNREACHABLE and "api_key" not in saved
        assert saved["providers"]["anthropic"] == {"model": "claude-sonnet-5-5", "api_key": "sk-ant-key"}

    def test_the_default_is_not_asked_about_itself(self, runner, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", config_path)
        from synapptic.providers import load_config, save_config
        save_config({"provider": "ollama", "model": "llama3", "api_url": self.UNREACHABLE})
        # the default preselected: Enter takes it; its model; its URL kept
        result = runner.invoke(cli, ["config", "provider"], input="\n1\n\n")
        assert result.exit_code == 0, result.output
        assert "Set default?" not in result.output and "Ollama (local) (default)" in result.output
        assert load_config()["provider"] == "ollama"


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


class TestIngestIntegrationTupleShape:

    def test_ingest_accepts_extended_integration_tuple(self, runner, monkeypatch):
        """ingest should handle integrate_archetypes tuples with extra fields."""
        from pathlib import Path

        monkeypatch.setattr("synapptic.cli.find_session_transcripts", lambda: {})
        monkeypatch.setattr("synapptic.cli.get_processed_session_ids", lambda: set())
        monkeypatch.setattr("synapptic.cli.get_queue", lambda: [])
        monkeypatch.setattr("synapptic.cli.clear_queue", lambda: None)
        monkeypatch.setattr("synapptic.cli.list_projects", lambda: [])
        monkeypatch.setattr(
            "synapptic.cli.load_profile",
            lambda project_slug=None: {"dimensions": {}, "metadata": {}},
        )
        monkeypatch.setattr("synapptic.providers.load_config", lambda: {"provider": "mock", "model": "mock"})
        monkeypatch.setattr(
            "synapptic.integrate.integrate_archetypes",
            lambda project_slugs=None: [
                ("demo", "copilot", Path("/tmp/demo"), "gemini-2.0-flash", 2)
            ],
        )

        result = runner.invoke(cli, ["ingest"])
        assert result.exit_code == 0
        assert "[demo]" in result.output
        assert "excluded=2" in result.output

