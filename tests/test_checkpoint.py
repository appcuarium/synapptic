"""Live-session checkpoints and the rebuilt merge, driven through the real CLI commands and hook scripts.

Real transcript files on disk, real filter / extract / state / profile code. Only the `claude` binary is
replaced: it is an external process that needs OAuth and the network, and the spy records each prompt it is sent.
"""

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from importlib.resources import files
from pathlib import Path

import pytest
from click.testing import CliRunner

from synapptic.cli import cli
from synapptic.providers import save_config
from synapptic.state import load_cursor, load_profile, save_observations, slug_from_project_dir

PROJECT_DIR_NAME = "-Users-someone-projects-demo"
SLUG = slug_from_project_dir(PROJECT_DIR_NAME)
SID = "33333333-4444-5555-6666-777777777777"
HOOKS = Path(str(files("synapptic") / "bundle" / "hooks"))


@pytest.fixture
def scratch(tmp_path, monkeypatch):
    """Every state path under tmp_path, a real Claude project folder, a real config."""
    # boundary: filesystem - the state and Claude projects dirs live under the user's HOME
    state = tmp_path / "synapptic"
    monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", state)
    monkeypatch.setattr("synapptic.state.GLOBAL_DIR", state / "global")
    monkeypatch.setattr("synapptic.state.PROJECTS_DIR", state / "projects")
    monkeypatch.setattr("synapptic.state.PROFILE_HISTORY_DIR", state / "profile_history")
    monkeypatch.setattr("synapptic.state.QUEUE_PATH", state / "queue.txt")
    monkeypatch.setattr("synapptic.state.CURSORS_DIR", state / "cursors")
    monkeypatch.setattr("synapptic.state.PIPELINE_LOCK_PATH", state / "pipeline.lock")
    monkeypatch.setattr("synapptic.state.CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    monkeypatch.setattr("synapptic.integrate.CLAUDE_PROJECTS_DIR", tmp_path / "claude_projects")
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", state / "config.yaml")
    (tmp_path / "claude_projects" / PROJECT_DIR_NAME).mkdir(parents=True)
    save_config({"provider": "claude-cli", "model": "haiku", "outputs": ["claude-code"]})
    return tmp_path


def transcript(scratch, session_id=SID):
    return scratch / "claude_projects" / PROJECT_DIR_NAME / f"{session_id}.jsonl"


def append_turns(path, pairs, when=None):
    """Append (assistant text, user text) pairs as real Claude Code transcript records."""
    stamp = (when or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    with open(path, "a") as f:
        for assistant_text, user_text in pairs:
            f.write(json.dumps({"type": "assistant", "timestamp": stamp,
                                "message": {"role": "assistant", "content": [{"type": "text", "text": assistant_text}]}}) + "\n")
            f.write(json.dumps({"type": "user", "timestamp": stamp,
                                "message": {"role": "user", "content": user_text}}) + "\n")


class ClaudeProcess:
    """Stands in for subprocess.run of the `claude` binary; records each prompt and the environment it got."""

    def __init__(self, observations=None, fail=False, during_call=None):
        self.observations, self.fail, self.during_call = observations or {}, fail, during_call
        self.prompts, self.envs = [], []

    def __call__(self, cmd, **kwargs):
        self.prompts.append(kwargs.get("input", ""))
        self.envs.append(kwargs.get("env") or {})
        if self.during_call:
            self.during_call(len(self.prompts))
        if self.fail:
            return subprocess.CompletedProcess(cmd, 1, stdout='{"result": "Not logged in"}', stderr="")
        texts = self.observations.get(len(self.prompts), [])
        result = json.dumps([observation(text) for text in texts])
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps({"result": result, "usage": {}}), stderr="")


def claude_spy(monkeypatch, observations=None, fail=False, during_call=None):
    """Replace the `claude` process. observations: {call number: [observation texts]}. Returns the spy."""
    spy = ClaudeProcess(observations, fail, during_call)
    # boundary: the `claude` CLI subprocess - an external binary that needs OAuth and the network
    monkeypatch.setattr("synapptic.providers.subprocess.run", spy)
    return spy


def merge_on_second_call(call_number):
    """What the session-end hook did on 2026-11-01: run `merge` while ingest was still extracting."""
    if call_number == 2:
        assert CliRunner().invoke(cli, ["merge"]).exit_code == 0


def observation(text):
    return {"dimension": "ai_failures", "observation": text, "confidence": 0.8, "evidence": text}


def checkpoint(*args):
    result = CliRunner().invoke(cli, ["checkpoint", "-s", SID, "--synthesize-after", "-1", *args])
    assert result.exit_code == 0, result.output
    return result


def chunk_files(scratch):
    return sorted(p.name for p in (scratch / "synapptic" / "projects" / SLUG / "observations").glob(f"{SID}@*.json"))


def test_second_checkpoint_sends_only_the_new_conversation(scratch, monkeypatch):
    path = transcript(scratch)
    append_turns(path, [("Here is the first plan.", "ALPHA no, read the file first")])
    prompts = claude_spy(monkeypatch, {1: ["Observation from call 1"], 2: ["Observation from call 2"]}).prompts

    checkpoint("--flush")
    append_turns(path, [("Second attempt done.", "BRAVO you skipped the test")])
    checkpoint("--flush")

    assert len(prompts) == 2
    assert "ALPHA" in prompts[0] and "BRAVO" not in prompts[0]
    assert "BRAVO" in prompts[1] and "ALPHA" not in prompts[1]
    assert len(chunk_files(scratch)) == 2
    assert load_cursor(SID) == path.stat().st_size


def test_checkpoint_waits_until_enough_new_conversation(scratch, monkeypatch):
    append_turns(transcript(scratch), [("Short answer.", "CHARLIE ok")])
    prompts = claude_spy(monkeypatch).prompts

    checkpoint("--min-tokens", "30000")

    assert prompts == []
    assert chunk_files(scratch) == []


def test_long_session_is_split_at_user_prompts_without_losing_or_repeating_text(scratch, monkeypatch):
    markers = [f"MARK{i:02d}" for i in range(8)]
    append_turns(transcript(scratch), [("A" * 150, f"{m} please redo this part") for m in markers])
    prompts = claude_spy(monkeypatch).prompts

    checkpoint("--flush", "--max-tokens", "100")

    assert len(prompts) > 1
    for marker in markers:
        assert sum(marker in prompt for prompt in prompts) == 1, marker


def test_failed_extraction_keeps_the_cursor_so_the_chunk_is_retried(scratch, monkeypatch):
    append_turns(transcript(scratch), [("Done, all green.", "DELTA you never ran it")])
    claude_spy(monkeypatch, fail=True)
    checkpoint("--flush")
    assert chunk_files(scratch) == []

    prompts = claude_spy(monkeypatch, {1: ["Claimed green without running"]}).prompts
    checkpoint("--flush")

    assert len(prompts) == 1 and "DELTA" in prompts[0], "the failed chunk was not retried"
    assert len(chunk_files(scratch)) == 1


def test_session_extracted_whole_before_cursors_resumes_after_that_extraction(scratch, monkeypatch):
    path = transcript(scratch)
    extracted_at = datetime.now(timezone.utc) - timedelta(hours=1)
    append_turns(path, [("Old answer.", "ECHO already extracted")], when=extracted_at - timedelta(hours=1))
    save_observations(SID, [observation("old")], project_slug=SLUG)
    whole = scratch / "synapptic" / "projects" / SLUG / "observations" / f"{SID}.json"
    os.utime(whole, (extracted_at.timestamp(), extracted_at.timestamp()))
    append_turns(path, [("New answer.", "FOXTROT written after the extraction")])
    prompts = claude_spy(monkeypatch).prompts

    checkpoint("--flush")

    assert len(prompts) == 1
    assert "FOXTROT" in prompts[0] and "ECHO" not in prompts[0]


def test_merge_running_during_ingest_does_not_count_a_session_twice(scratch, monkeypatch):
    """The 2026-11-01 overlap: the session-end hook merged while a manual ingest was still extracting."""
    first, second = "44444444-0000-0000-0000-000000000001", "44444444-0000-0000-0000-000000000002"
    append_turns(transcript(scratch, first), [("x" * 400, "GOLF first session correction")] * 3)
    append_turns(transcript(scratch, second), [("y" * 100, "HOTEL second session")])

    per_call = {1: "Ran pytest on the host instead of the oneoff container",
                2: "Rewrote a strip component that TileStripShell already provides"}
    claude_spy(monkeypatch, {n: [text] for n, text in per_call.items()}, during_call=merge_on_second_call)

    result = CliRunner().invoke(cli, ["ingest", "--min-lines", "1"])
    assert result.exit_code == 0, result.output

    prefs = load_profile(project_slug=SLUG)["dimensions"]["ai_failures"]
    first_pref = next(p for p in prefs if p["observation"] == per_call[1])
    assert first_pref["evidence_count"] == 1


def test_merge_twice_leaves_the_profile_unchanged(scratch):
    save_observations(SID, [observation("Repeated the same mistake")], project_slug=SLUG)
    assert CliRunner().invoke(cli, ["merge"]).exit_code == 0
    before = load_profile(project_slug=SLUG)

    second = CliRunner().invoke(cli, ["merge"])

    assert "No new observations" in second.output
    assert load_profile(project_slug=SLUG) == before


def run_hook(tmp_path, script, payload):
    """Run a bundled hook script with a `synapptic` on PATH that records the commands it is asked to run."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    calls = tmp_path / "calls.txt"
    # boundary: the synapptic executable the hook spawns is a separate process; this one records each command
    (bin_dir / "synapptic").write_text(
        f"#!{sys.executable}\nimport sys\nopen({str(calls)!r}, 'a').write(' '.join(sys.argv[1:]) + '\\n')\n")
    # boundary: network - the session-end hook notifies a local relay with curl; never reach a real one
    (bin_dir / "curl").write_text("#!/bin/sh\nexit 0\n")
    for tool in ("synapptic", "curl"):
        (bin_dir / tool).chmod(0o755)
    log = tmp_path / "hook.log"
    # boundary: env - PATH, HOME and the hook's lock/log files point at tmp_path, never at the live /tmp ones
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", HOME=str(tmp_path),
               SYNAPPTIC_HOOK_PIDFILE=str(tmp_path / "hook.pid"), SYNAPPTIC_HOOK_LOGFILE=str(log))
    subprocess.run(["bash", str(HOOKS / script)], input=json.dumps(payload), text=True, env=env, check=True, timeout=30)
    deadline = time.time() + 20
    while time.time() < deadline and not (calls.exists() and calls.read_text().strip()):
        time.sleep(0.2)
    time.sleep(0.5)
    return calls.read_text().splitlines() if calls.exists() else []


@pytest.mark.parametrize("event, expected", [
    ("Stop", f"checkpoint -s {SID}"),
    ("PreCompact", f"checkpoint -s {SID} --flush"),
])
def test_checkpoint_hook_runs_checkpoint_for_the_session(tmp_path, event, expected):
    steps = run_hook(tmp_path, "synapptic-checkpoint.sh", {"session_id": SID, "hook_event_name": event})
    assert steps == [expected]


def test_synapptic_marks_its_own_claude_runs(scratch, monkeypatch):
    spy = claude_spy(monkeypatch)
    append_turns(transcript(scratch), [("Plan.", "INDIA no")])

    checkpoint("--flush")

    assert spy.envs and spy.envs[0].get("SYNAPPTIC_INTERNAL") == "1"


@pytest.mark.parametrize("script, payload", [
    ("synapptic-checkpoint.sh", {"session_id": SID, "hook_event_name": "Stop"}),
    ("synapptic-session-end.sh", {"session_id": SID, "reason": "prompt_input_exit", "transcript_path": "/x/y.jsonl"}),
])
def test_hooks_ignore_synapptics_own_claude_runs(tmp_path, monkeypatch, script, payload):
    # boundary: env - the variable synapptic sets on the `claude -p` processes it starts, inherited by hooks
    monkeypatch.setenv("SYNAPPTIC_INTERNAL", "1")
    assert run_hook(tmp_path, script, payload) == []


def test_session_end_hook_flushes_through_checkpoint(tmp_path):
    payload = {"session_id": SID, "reason": "prompt_input_exit",
               "transcript_path": str(tmp_path / "claude_projects" / PROJECT_DIR_NAME / f"{SID}.jsonl")}
    steps = run_hook(tmp_path, "synapptic-session-end.sh", payload)
    assert steps[0] == f"checkpoint -s {SID} --flush --synthesize-after -1"


def test_install_registers_the_checkpoint_hook_on_stop_and_precompact(tmp_path, monkeypatch):
    # boundary: env - install writes under HOME/.claude; point it at tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", tmp_path / "synapptic")
    monkeypatch.setattr("synapptic.state.GLOBAL_DIR", tmp_path / "synapptic" / "global")
    monkeypatch.setattr("synapptic.state.PROJECTS_DIR", tmp_path / "synapptic" / "projects")
    monkeypatch.setattr("synapptic.state.PROFILE_HISTORY_DIR", tmp_path / "synapptic" / "profile_history")

    assert CliRunner().invoke(cli, ["install"]).exit_code == 0

    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]
    commands = {event: [h["command"] for entry in entries for h in entry["hooks"]] for event, entries in hooks.items()}
    # the checkpoint script is on Stop and PreCompact; install registers the harness's own Stop entry next to it
    assert "~/.claude/hooks/synapptic-checkpoint.sh" in commands["Stop"]
    assert commands["PreCompact"] == ["~/.claude/hooks/synapptic-checkpoint.sh"]
    assert (tmp_path / ".claude" / "hooks" / "synapptic-checkpoint.sh").exists()
