"""Which Claude Code transcripts count as the user's sessions.

Rule 1: a transcript whose working folder is in the temp folder (agent scratchpads, probes) is never
a session. Rule 2: a headless run (entrypoint "sdk-cli": `claude -p`, synapptic's own calls, scripted agents)
is skipped unless the user opts in with config["include_headless_sessions"].
"""

import json

import yaml

from synapptic import state
from synapptic.sessions import indexer, parse
from synapptic.sessions.store import SessionStore

REAL_CWD = "/Users/someone/code/app"
SCRATCHPAD_CWD = "/private/tmp/claude-501/-Users-someone-code-app/0a1b2c3d/scratchpad/probe/C1"


def write_transcript(projects_dir, project, session_id, cwd, entrypoint):
    folder = projects_dir / project
    folder.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "user", "cwd": cwd, "entrypoint": entrypoint, "sessionId": session_id,
         "timestamp": "2026-11-01T10:00:00Z", "message": {"role": "user", "content": "add a row of cards"}},
        {"type": "assistant", "cwd": cwd, "entrypoint": entrypoint, "sessionId": session_id,
         "timestamp": "2026-11-01T10:00:05Z",
         "message": {"role": "assistant", "model": "claude-opus-5-5", "content": [{"type": "text", "text": "done"}]}},
    ]
    path = folder / f"{session_id}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


def make_projects(tmp_path, monkeypatch):
    projects_dir = tmp_path / ".claude" / "projects"
    projects_dir.mkdir(parents=True)
    monkeypatch.setattr("synapptic.state.CLAUDE_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("synapptic.sessions.indexer.CLAUDE_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("synapptic.config.CLAUDE_PROJECTS_DIR", projects_dir)
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / ".synapptic" / "config.yaml")
    write_transcript(projects_dir, "-Users-someone-code-app", "interactive", REAL_CWD, "cli")
    write_transcript(projects_dir, "-Users-someone-code-app", "desktop", REAL_CWD, "claude-desktop")
    write_transcript(projects_dir, "-Users-someone-code-app", "headless", REAL_CWD, "sdk-cli")
    write_transcript(projects_dir, "-private-tmp-claude-501-scratchpad-probe-C1", "scratchpad", SCRATCHPAD_CWD, "cli")
    return projects_dir


def test_temp_folder_sessions_are_not_found(tmp_path, monkeypatch):
    make_projects(tmp_path, monkeypatch)
    assert "scratchpad" not in state.find_session_transcripts(include_headless=False)


def test_headless_sessions_are_not_found_by_default(tmp_path, monkeypatch):
    make_projects(tmp_path, monkeypatch)
    assert "headless" not in state.find_session_transcripts()


def test_interactive_and_desktop_sessions_are_found(tmp_path, monkeypatch):
    # RED-EXEMPT: guards that the filter keeps the user's real sessions; there is no defect to reproduce.
    make_projects(tmp_path, monkeypatch)
    found = state.find_session_transcripts()
    assert {"interactive", "desktop"} <= set(found)


def test_headless_sessions_are_found_when_opted_in_but_temp_folders_never(tmp_path, monkeypatch):
    make_projects(tmp_path, monkeypatch)
    found = state.find_session_transcripts(include_headless=True)
    assert "headless" in found
    assert "scratchpad" not in found


def test_opt_in_is_read_from_config(tmp_path, monkeypatch):
    make_projects(tmp_path, monkeypatch)
    config_path = tmp_path / ".synapptic" / "config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(yaml.safe_dump({"provider": "claude-cli", "include_headless_sessions": True}))
    assert "headless" in state.find_session_transcripts()


def test_indexer_skips_excluded_and_removes_ones_indexed_before(tmp_path, monkeypatch):
    projects_dir = make_projects(tmp_path, monkeypatch)
    db_path = tmp_path / "proxy.db"
    monkeypatch.setattr("synapptic.sessions.indexer.DB_PATH", db_path)
    store = SessionStore(db_path)
    store.init_db()
    stale = projects_dir / "-private-tmp-claude-501-scratchpad-probe-C1" / "scratchpad.jsonl"
    store.index_session(session_id="scratchpad", project="scratchpad-probe-C1", project_dir=stale.parent.name,
                        title="add a row of cards", model="claude-opus-5-5", first_ts="", last_ts="",
                        msg_count=2, file_size=stale.stat().st_size, file_path=str(stale))
    store.close()

    indexer.index_all_sessions(incremental=True)

    store = SessionStore(db_path)
    store.init_db()
    indexed = store.get_indexed_session_ids()
    store.close()
    assert indexed == {"interactive", "desktop"}


def test_browser_file_scan_skips_excluded(tmp_path, monkeypatch):
    make_projects(tmp_path, monkeypatch)
    monkeypatch.setitem(parse.session_cache, "data", None)
    listed = {s["id"] for s in parse.list_all_sessions()}
    assert listed == {"interactive", "desktop"}
