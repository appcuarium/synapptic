"""A failed extraction must not be recorded as a processed session; a genuinely empty one must.

Real transcript discovery, real config loading, real call_llm -> call_claude_cli -> JSON parsing.
Only the `claude` binary is stubbed, because it is an external process that needs OAuth and the network.
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from synapptic.cli import cli
from synapptic.providers import call_claude_cli, save_config
from synapptic.state import get_processed_session_ids

FIXTURE = Path(__file__).parent / "fixtures" / "sample_session.jsonl"
SID = "11111111-2222-3333-4444-555555555555"
NOT_LOGGED_IN = json.dumps({"type": "result", "is_error": True, "result": "Not logged in · Please run /login"})


@pytest.fixture
def scratch_state(tmp_path, monkeypatch):
    """Point every state path at tmp_path, put a real transcript under a real project dir, save a real config."""
    # boundary: filesystem - the state and Claude projects dirs live under the user's HOME
    monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", tmp_path / "synapptic")
    monkeypatch.setattr("synapptic.state.GLOBAL_DIR", tmp_path / "synapptic" / "global")
    monkeypatch.setattr("synapptic.state.PROJECTS_DIR", tmp_path / "synapptic" / "projects")
    monkeypatch.setattr("synapptic.state.PROFILE_HISTORY_DIR", tmp_path / "synapptic" / "profile_history")
    monkeypatch.setattr("synapptic.state.QUEUE_PATH", tmp_path / "synapptic" / "queue.txt")
    monkeypatch.setattr("synapptic.state.CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    monkeypatch.setattr("synapptic.integrate.CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "synapptic" / "config.yaml")
    project = tmp_path / "claude_projects" / "-Users-someone-projects-demo"
    project.mkdir(parents=True)
    shutil.copy(FIXTURE, project / f"{SID}.jsonl")
    save_config({"provider": "claude-cli", "model": "haiku"})
    return tmp_path


class ClaudeBinary:
    """Stands in for subprocess.run of the `claude` binary: answers (returncode, stdout), records each argv."""

    def __init__(self, returncode, stdout):
        self.returncode, self.stdout, self.calls = returncode, stdout, []

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.stdout, stderr="")


def claude_binary(monkeypatch, returncode, stdout):
    """Replace the external `claude` process with one that answers (returncode, stdout); returns the argv list."""
    binary = ClaudeBinary(returncode, stdout)
    # boundary: the `claude` CLI subprocess - an external binary that needs OAuth and the network
    monkeypatch.setattr("synapptic.providers.subprocess.run", binary)
    return binary.calls


def test_extract_failed_llm_call_leaves_session_unprocessed(scratch_state, monkeypatch):
    claude_binary(monkeypatch, 1, NOT_LOGGED_IN)
    result = CliRunner().invoke(cli, ["extract", "-s", SID, "--min-lines", "1"])
    assert result.exit_code == 0, result.output
    assert SID not in get_processed_session_ids()


def test_extract_genuinely_empty_result_marks_session_processed(scratch_state, monkeypatch):
    claude_binary(monkeypatch, 0, json.dumps({"type": "result", "result": "[]", "usage": {}}))
    result = CliRunner().invoke(cli, ["extract", "-s", SID, "--min-lines", "1"])
    assert result.exit_code == 0, result.output
    assert SID in get_processed_session_ids()


def test_ingest_failed_llm_call_leaves_session_unprocessed(scratch_state, monkeypatch):
    claude_binary(monkeypatch, 1, NOT_LOGGED_IN)
    result = CliRunner().invoke(cli, ["ingest", "--min-lines", "1"])
    assert result.exit_code == 0, result.output
    assert SID not in get_processed_session_ids()


def test_claude_cli_provider_does_not_pass_bare(monkeypatch):
    """--bare skips OAuth and keychain, so plan-authenticated users get 'Not logged in'."""
    calls = claude_binary(monkeypatch, 0, json.dumps({"type": "result", "result": "ok", "usage": {}}))
    assert call_claude_cli("say ok", model="haiku") == "ok"
    assert "--bare" not in calls[0]
