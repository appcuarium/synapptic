"""The guards commands are plumbing around the separately installed synapptic-core package: without it, they
say so (the one message every module uses) instead of failing somewhere inside, and `stats` and `diff` still
answer with their own numbers."""

import yaml
from click.testing import CliRunner

from synapptic import core as core_module
from synapptic.cli import cli


def test_guards_say_when_the_core_package_is_missing(monkeypatch):
    # boundary: the compiled core is a separately installed package; here it is absent
    monkeypatch.setattr(core_module, "core", None)
    result = CliRunner().invoke(cli, ["guards"])
    assert result.exit_code != 0
    assert "synapptic-core package is not installed" in result.output


def test_a_rule_change_cannot_be_rolled_back_without_the_core(monkeypatch):
    monkeypatch.setattr(core_module, "core", None)
    result = CliRunner().invoke(cli, ["rollback", "v1"])
    assert result.exit_code != 0
    assert "synapptic-core package is not installed" in result.output


def test_the_tuning_dial_is_config_and_needs_no_core(tmp_path, monkeypatch):
    # boundary: the config file lives under a scratch path, never the user's ~/.synapptic
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
    monkeypatch.setattr(core_module, "core", None)
    runner = CliRunner()
    assert runner.invoke(cli, ["config", "tuning"]).output == "Tuning: normal\n"
    assert runner.invoke(cli, ["config", "tuning", "eager"]).output == "Tuning: eager\n"
    assert runner.invoke(cli, ["config", "tuning"]).output == "Tuning: eager\n"
    assert yaml.safe_load((tmp_path / "config.yaml").read_text())["harness"] == {"tuning": "eager"}
    assert runner.invoke(cli, ["config", "tuning", "wild"]).exit_code != 0
    assert "Tuning:          eager" in runner.invoke(cli, ["config", "show"]).output


def test_the_learning_step_says_when_the_core_is_missing(monkeypatch):
    # the SessionEnd hook runs `synapptic learn`; without the core it reports and exits clean, as ingest does
    monkeypatch.setattr(core_module, "core", None)
    result = CliRunner().invoke(cli, ["learn"])
    assert result.exit_code == 0
    assert "skipped: " in result.output and "synapptic-core package is not installed" in result.output
