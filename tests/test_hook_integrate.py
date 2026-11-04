"""The session-end hook must end with integrate for the closed session's project, so memory is refreshed
without a manual ingest; `synapptic integrate -p <project>` writes user_archetype.md into that project's memory."""

import json
import os
import subprocess
import sys
import time
from importlib.resources import files
from pathlib import Path

import pytest
from click.testing import CliRunner

from synapptic.cli import cli
from synapptic.providers import save_config
from synapptic.state import save_archetype, slug_from_project_dir

HOOK = Path(str(files("synapptic") / "bundle" / "hooks" / "synapptic-session-end.sh"))
SID = "22222222-3333-4444-5555-666666666666"
PROJECT_DIR_NAME = "-Users-someone-projects-demo"
SLUG = slug_from_project_dir(PROJECT_DIR_NAME)


@pytest.fixture
def scratch_state(tmp_path, monkeypatch):
    """Every state path under tmp_path; a real Claude project folder with a memory dir; a real config."""
    # boundary: filesystem - the state and Claude projects dirs live under the user's HOME
    monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", tmp_path / "synapptic")
    monkeypatch.setattr("synapptic.state.GLOBAL_DIR", tmp_path / "synapptic" / "global")
    monkeypatch.setattr("synapptic.state.PROJECTS_DIR", tmp_path / "synapptic" / "projects")
    monkeypatch.setattr("synapptic.state.PROFILE_HISTORY_DIR", tmp_path / "synapptic" / "profile_history")
    monkeypatch.setattr("synapptic.state.CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    monkeypatch.setattr("synapptic.integrate.CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "synapptic" / "config.yaml")
    (tmp_path / "claude_projects" / PROJECT_DIR_NAME / "memory").mkdir(parents=True)
    save_config({"provider": "claude-cli", "model": "haiku", "outputs": ["claude-code"]})
    return tmp_path


def test_integrate_command_writes_project_memory(scratch_state):
    save_archetype("## User Archetype\n\nGLOBAL-PORTRAIT", project_slug=None)
    save_archetype("## Guards\n\nPROJECT-GUARD", project_slug=SLUG)

    result = CliRunner().invoke(cli, ["integrate", "-p", SLUG])

    assert result.exit_code == 0, result.output
    written = (scratch_state / "claude_projects" / PROJECT_DIR_NAME / "memory" / "user_archetype.md").read_text()
    assert "GLOBAL-PORTRAIT" in written and "PROJECT-GUARD" in written


def test_session_end_hook_runs_integrate_for_the_closed_project(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    calls = tmp_path / "calls.txt"
    # boundary: the synapptic executable the hook spawns is a separate process; this one records each step it is asked for
    (bin_dir / "synapptic").write_text(
        f"#!{sys.executable}\nimport sys\nopen({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n")
    # boundary: network - the hook notifies a local relay with curl; never reach a real one from a test
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 0\n")
    for tool in ("synapptic", "curl"):
        (bin_dir / tool).chmod(0o755)
    log = tmp_path / "hook.log"
    transcript = tmp_path / "claude_projects" / PROJECT_DIR_NAME / f"{SID}.jsonl"
    payload = json.dumps({"session_id": SID, "reason": "prompt_input_exit", "transcript_path": str(transcript)})
    # boundary: env - PATH, HOME and the hook's lock/log files point at tmp_path, never at the live /tmp ones
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(tmp_path),
               SYNAPPTIC_HOOK_PIDFILE=str(tmp_path / "hook.pid"), SYNAPPTIC_HOOK_LOGFILE=str(log))

    subprocess.run(["bash", str(HOOK)], input=payload, text=True, env=env, check=True, timeout=30)
    deadline = time.time() + 20
    while time.time() < deadline and not (log.exists() and f"Done {SID}" in log.read_text()):
        time.sleep(0.2)

    steps = calls.read_text().splitlines()
    # integrate for the closed project, then the learning step for the closed session (3e, 2026-11-04)
    assert steps[-3:] == [f"synthesize -p {SLUG}", f"integrate -p {SLUG}", f"learn -s {SID}"], steps
