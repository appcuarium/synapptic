"""Rules on/off: synapptic keeps learning, and only writes rules into the coding tools when write_rules is on.
Its output is generated from ~/.synapptic and marked as such; turning rules off withdraws exactly what it
generated (Agent OS style: sources kept, generated files disposable)."""

import json
import shutil
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fastapi.testclient import TestClient

from synapptic.cli import cli
from synapptic.dashboard.app import create_app
from synapptic.integrate import GENERATED_NOTICE, integrate_archetypes, withdraw_rules
from synapptic.providers import load_config, save_config
from synapptic.state import save_archetype, slug_from_project_dir

ARCHETYPE = "## Guards\n- ALWAYS read the file before editing it"
USER_MEMORY = "# Memory Index\n\n- [feedback](feedback.md) - keep this line\n"
USER_AGENTS = "# Agents\n\nMy own instructions for Codex.\n"


OUTPUTS = ["claude-code", "codex", "cline"]


@pytest.fixture
def project(monkeypatch):
    """A real setup read by the real code: synapptic's state with a saved archetype and config, a Claude Code
    project folder whose name encodes a real project root, and that project's memory with the user's own index."""
    # Claude Code names a project folder after its path with "/" as "-", so the path must hold no "-" to
    # decode back; pytest's tmp_path does ("pytest-of-<user>"), the system temp folder does not.
    home = Path(tempfile.mkdtemp(prefix="synrules")).resolve()
    assert "-" not in str(home), f"temp folder {home} cannot be encoded as a Claude Code project name"
    # boundary: env - HOME is the security boundary resolve_project_root checks project roots against
    monkeypatch.setenv("HOME", str(home))
    # boundary: filesystem - the state, config, rules and Claude projects dirs live under the user's HOME
    monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", home / ".synapptic")
    monkeypatch.setattr("synapptic.state.GLOBAL_DIR", home / ".synapptic" / "global")
    monkeypatch.setattr("synapptic.state.PROJECTS_DIR", home / ".synapptic" / "projects")
    monkeypatch.setattr("synapptic.state.PROFILE_HISTORY_DIR", home / ".synapptic" / "profile_history")
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", home / ".synapptic" / "config.yaml")
    monkeypatch.setattr("synapptic.integrate.RULES_DIR", home / ".synapptic" / "rules")
    monkeypatch.setattr("synapptic.integrate.CLAUDE_PROJECTS_DIR", home / ".claude" / "projects")
    root = home / "repo"
    root.mkdir()
    encoded = "-" + str(root).lstrip("/").replace("_", "--").replace("/", "-")
    memory_dir = home / ".claude" / "projects" / encoded / "memory"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MEMORY.md").write_text(USER_MEMORY)
    save_config({"outputs": OUTPUTS})
    save_archetype(ARCHETYPE, project_slug=None)
    yield {"memory": memory_dir, "root": root,
           "rules": home / ".synapptic" / "rules" / slug_from_project_dir(encoded)}
    shutil.rmtree(home, ignore_errors=True)


def test_rules_off_keeps_writing_the_rules_but_plugs_nothing_into_the_tools(project):
    save_config({"outputs": OUTPUTS, "write_rules": False})
    updated = integrate_archetypes()
    # still written, in synapptic's own rules folder
    assert {entry[1] for entry in updated} == {"claude-code", "codex", "cline"}
    assert all(entry[2].parent == project["rules"] for entry in updated)
    assert ARCHETYPE in (project["rules"] / "claude-code" / "user_archetype.md").read_text()
    assert ARCHETYPE in (project["rules"] / "codex" / "AGENTS.md").read_text()
    # not deployed: the tools never see it
    assert not (project["memory"] / "user_archetype.md").exists()
    assert not (project["root"] / "AGENTS.md").exists()
    assert (project["memory"] / "MEMORY.md").read_text() == USER_MEMORY


def test_rules_on_deploys_and_keeps_the_rules_folder(project):
    integrate_archetypes()
    assert ARCHETYPE in (project["memory"] / "user_archetype.md").read_text()
    assert ARCHETYPE in (project["rules"] / "claude-code" / "user_archetype.md").read_text()


def test_generated_output_says_it_is_generated(project):
    integrate_archetypes()
    assert GENERATED_NOTICE in (project["memory"] / "user_archetype.md").read_text()
    assert GENERATED_NOTICE in (project["root"] / "AGENTS.md").read_text()
    assert GENERATED_NOTICE in (project["root"] / ".clinerules").read_text()


def test_withdraw_removes_only_what_synapptic_generated(project):
    memory, root = project["memory"], project["root"]
    (root / "AGENTS.md").write_text(USER_AGENTS)
    (root / "CONVENTIONS.md").write_text("# Conventions\nWritten by the user, no synapptic notice.\n")
    legacy_cursor = root / ".cursor" / "rules" / "synapptic.mdc"
    legacy_cursor.parent.mkdir(parents=True)
    legacy_cursor.write_text("---\nalwaysApply: true\n---\n\nold rules, written before the notice existed\n")
    integrate_archetypes()
    # a pointer the user reworded: it still links the generated file, so it goes too
    index = memory / "MEMORY.md"
    index.write_text(index.read_text() + "- [User archetype](user_archetype.md) - senior trader, blunt\n")

    withdraw_rules()

    assert not (memory / "user_archetype.md").exists()
    assert "user_archetype.md" not in index.read_text() and "keep this line" in index.read_text()
    assert (root / "AGENTS.md").read_text() == USER_AGENTS            # only the marked section went
    assert not (root / ".clinerules").exists()                        # carried the notice: generated
    assert not legacy_cursor.exists()                                 # synapptic.mdc is synapptic's by name
    assert (root / "CONVENTIONS.md").exists()                         # no notice: may be the user's


def test_rules_on_again_regenerates_from_the_sources(project):
    integrate_archetypes()
    withdraw_rules()
    integrate_archetypes()
    assert ARCHETYPE in (project["memory"] / "user_archetype.md").read_text()
    assert "user_archetype.md" in (project["memory"] / "MEMORY.md").read_text()
    assert ARCHETYPE in (project["root"] / "AGENTS.md").read_text()


def test_config_rules_off_then_on(project):
    runner = CliRunner()
    integrate_archetypes()

    off = runner.invoke(cli, ["config", "rules", "off"])
    assert off.exit_code == 0, off.output
    assert load_config()["write_rules"] is False
    assert not (project["memory"] / "user_archetype.md").exists()
    assert "user_archetype.md" not in (project["memory"] / "MEMORY.md").read_text()
    assert runner.invoke(cli, ["config", "rules"]).output.strip() == "Rules: off"
    # still learning: the next integrate (as ingest runs it) writes the rules folder, not the project
    runner.invoke(cli, ["integrate"])
    assert ARCHETYPE in (project["rules"] / "claude-code" / "user_archetype.md").read_text()
    assert not (project["memory"] / "user_archetype.md").exists()

    on = runner.invoke(cli, ["config", "rules", "on"])
    assert on.exit_code == 0, on.output
    assert load_config()["write_rules"] is True
    assert ARCHETYPE in (project["memory"] / "user_archetype.md").read_text()
    assert ARCHETYPE in (project["root"] / "AGENTS.md").read_text()


def test_uninstall_withdraws_every_tools_rules_and_install_brings_them_back(project):
    runner = CliRunner()
    integrate_archetypes()
    index = project["memory"] / "MEMORY.md"
    index.write_text(index.read_text().replace("Auto-generated user archetype from session transcripts (synapptic)",
                                               "who I am"))   # a reworded pointer

    uninstall = runner.invoke(cli, ["uninstall"], input="n\n")   # keep the artifacts
    assert uninstall.exit_code == 0, uninstall.output
    assert not (project["memory"] / "user_archetype.md").exists()
    assert "user_archetype.md" not in index.read_text()
    assert not (project["root"] / "AGENTS.md").exists() and not (project["root"] / ".clinerules").exists()

    install = runner.invoke(cli, ["install"])
    assert install.exit_code == 0, install.output
    assert ARCHETYPE in (project["memory"] / "user_archetype.md").read_text()
    assert ARCHETYPE in (project["root"] / "AGENTS.md").read_text()


def ws_request(ws, request_id, method, params=None):
    ws.send_text(json.dumps({"type": "request", "id": request_id, "method": method, "params": params or {}}))
    return ws.receive_json()


def test_the_dashboard_switch_withdraws_and_deploys(project, tmp_path):
    integrate_archetypes()
    with TestClient(create_app(static_dir=tmp_path / "no-build")) as client, client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert ws_request(ws, "g1", "config.get")["result"]["write_rules"] is True   # default, not in the file
        off = ws_request(ws, "u1", "config.update", {"write_rules": False})["result"]
        assert off["write_rules"] is False and load_config()["write_rules"] is False
        assert not (project["memory"] / "user_archetype.md").exists()
        assert not (project["root"] / "AGENTS.md").exists()
        on = ws_request(ws, "u2", "config.update", {"write_rules": True})["result"]
    assert on["write_rules"] is True
    assert ARCHETYPE in (project["memory"] / "user_archetype.md").read_text()
    assert ARCHETYPE in (project["root"] / "AGENTS.md").read_text()
