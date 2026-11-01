"""The dashboard's session browser over /api/ws: sessions.projects, sessions.list, sessions.turns."""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from synapptic.dashboard import handlers
from synapptic.dashboard.app import create_app
from synapptic.sessions.store import SessionStore

TRANSCRIPT = [
    {"type": "user", "timestamp": "2026-10-31T17:21:00Z", "uuid": "u1",
     "message": {"role": "user", "content": "how do I start the relay?"}},
    {"type": "assistant", "timestamp": "2026-10-31T17:22:00Z", "uuid": "a1",
     "message": {"role": "assistant", "model": "claude-opus-5-5",
                 "usage": {"input_tokens": 6, "output_tokens": 1200, "cache_read_input_tokens": 212400},
                 "content": [{"type": "thinking", "thinking": "private reasoning"},
                             {"type": "tool_use", "id": "t1", "name": "Bash", "input": {"command": "ls src/synapptic/relay"}}]}},
    {"type": "user", "timestamp": "2026-10-31T17:22:05Z", "uuid": "u2",
     "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "app.py\nparse.py"}]}},
    {"type": "assistant", "timestamp": "2026-10-31T17:22:10Z", "uuid": "a2",
     "message": {"role": "assistant", "model": "claude-opus-5-5",
                 "usage": {"input_tokens": 4, "output_tokens": 640},
                 "content": [{"type": "text", "text": "Run `synapptic relay start -d`."}]}},
    {"type": "system", "subtype": "compact_boundary", "timestamp": "2026-10-31T17:24:00Z", "uuid": "c1",
     "compactMetadata": {"trigger": "auto", "preTokens": 167200}},
    {"type": "user", "timestamp": "2026-10-31T17:25:00Z", "uuid": "u3",
     "message": {"role": "user", "content": "http://127.0.0.1:5100/ -> errors"}},
]


@pytest.fixture
def indexed(tmp_path, monkeypatch):
    """A session index with two sessions in two projects; only the first has a transcript on disk."""
    db_path = tmp_path / "proxy.db"
    monkeypatch.setattr(handlers, "DB_PATH", db_path)
    handlers.parsed_session_cache.clear()
    transcript = tmp_path / "dc88c6b4.jsonl"
    transcript.write_text("\n".join(json.dumps(record) for record in TRANSCRIPT) + "\n")
    store = SessionStore(db_path)
    store.init_db()
    store.index_session("dc88c6b4", "appcuarium-synapptic", "-Users-sorin-synapptic", "how do I start the relay?",
                        "claude-opus-5-5", "2026-10-31T17:21:00Z", "2026-10-31T17:25:00Z", 5,
                        transcript.stat().st_size, str(transcript))
    store.index_session("a8f68aa4", "machine-be", "-Users-sorin-machine-be", "analyze the releases",
                        "claude-opus-5-5", "2026-10-30T09:57:24Z", "2026-10-30T10:40:00Z", 131,
                        1_423_000, str(tmp_path / "gone.jsonl"))
    store.close()
    return transcript


@pytest.fixture
def client(indexed, tmp_path):
    with TestClient(create_app(static_dir=tmp_path / "no-build")) as test_client:
        yield test_client


def request(ws, request_id, method, params=None):
    ws.send_text(json.dumps({"type": "request", "id": request_id, "method": method, "params": params or {}}))
    return ws.receive_json()


def test_projects_and_sessions_come_from_the_index_without_file_paths(client, indexed):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        projects = request(ws, "p", "sessions.projects")["result"]
        everything = request(ws, "a", "sessions.list")["result"]
        one_project = request(ws, "b", "sessions.list", {"project": "machine-be"})["result"]
    assert [(p["project"], p["sessions"]) for p in projects] == [("appcuarium-synapptic", 1), ("machine-be", 1)]
    assert [s["session_id"] for s in everything] == ["dc88c6b4", "a8f68aa4"]   # newest first
    assert [s["session_id"] for s in one_project] == ["a8f68aa4"]
    assert everything[0]["message_count"] == 5 and everything[0]["title"] == "how do I start the relay?"
    assert str(indexed) not in json.dumps(everything) and "file_path" not in everything[0]


def test_turns_come_in_pages_with_the_session_stats(client):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        first = request(ws, "t1", "sessions.turns", {"session_id": "dc88c6b4", "limit": 2})["result"]
        rest = request(ws, "t2", "sessions.turns", {"session_id": "dc88c6b4", "offset": 2, "limit": 2})["result"]
    assert first["total"] == 4 and first["offset"] == 0 and rest["offset"] == 2
    assert [t["role"] for t in first["turns"] + rest["turns"]] == ["user", "assistant", "system", "user"]
    assistant = first["turns"][1]
    # what the page shows: text and tool chips; thinking and tool output stay on the server
    assert assistant["blocks"] == [
        {"type": "tool_use", "name": "Bash", "summary": "command: ls src/synapptic/relay"},
        {"type": "text", "text": "Run `synapptic relay start -d`."},
    ]
    assert assistant["model"] == "claude-opus-5-5" and assistant["usage"]["output_tokens"] == 1840
    assert rest["turns"][0]["blocks"] == [{"type": "compaction", "trigger": "auto", "pre_tokens": 167200}]
    # two API responses (the tool call, then the answer after its result): two requests, not one merged turn
    assert first["stats"]["total_requests"] == 2 and first["stats"]["total_cache_read_tokens"] == 212400
    assert first["session"]["session_id"] == "dc88c6b4" and "file_path" not in first["session"]


@pytest.fixture
def projects_dir(indexed, tmp_path, monkeypatch):
    """Claude Code's projects folder: dc88c6b4 written just now, a8f68aa4 an hour ago, 7e57f00d new and not indexed."""
    folder = tmp_path / "projects" / "-Users-sorin-synapptic"
    folder.mkdir(parents=True)
    body = "\n".join(json.dumps(record) for record in TRANSCRIPT) + "\n"
    for session_id in ("dc88c6b4", "a8f68aa4", "7e57f00d"):
        (folder / f"{session_id}.jsonl").write_text(body)
    hour_ago = time.time() - 3600
    os.utime(folder / "a8f68aa4.jsonl", (hour_ago, hour_ago))
    monkeypatch.setattr(handlers, "CLAUDE_PROJECTS_DIR", tmp_path / "projects")
    return folder


def test_sessions_written_in_the_last_five_minutes_are_live_and_new_ones_appear(client, projects_dir):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        sessions = {s["session_id"]: s for s in request(ws, "l1", "sessions.list")["result"]}
    assert sessions["dc88c6b4"]["live"] is True
    assert sessions["a8f68aa4"]["live"] is False   # last written an hour ago
    assert sessions["7e57f00d"]["live"] is True    # not indexed yet: indexed when seen live
    assert sessions["7e57f00d"]["title"] == "how do I start the relay?" and sessions["7e57f00d"]["message_count"] == 5


def test_sessions_carry_their_turn_count(client, projects_dir):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        sessions = {s["session_id"]: s for s in request(ws, "c1", "sessions.list")["result"]}
    # 7e57f00d is indexed when seen live: you, the agent, the compaction marker, you
    assert sessions["7e57f00d"]["turn_count"] == 4


def test_a_session_the_end_hook_closed_is_not_live(client, projects_dir):
    store = SessionStore(handlers.DB_PATH)
    store.init_db()
    store.add_ended_session("dc88c6b4")
    store.close()
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        sessions = {s["session_id"]: s for s in request(ws, "l2", "sessions.list")["result"]}
    assert sessions["dc88c6b4"]["live"] is False and sessions["7e57f00d"]["live"] is True


def test_the_end_hook_can_report_a_session_ended_to_the_dashboard(client, projects_dir):
    reply = client.post("/api/session-ended", json={"session_id": "dc88c6b4"})
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        sessions = {s["session_id"]: s for s in request(ws, "e1", "sessions.list")["result"]}
    assert reply.status_code == 200
    assert sessions["dc88c6b4"]["live"] is False and sessions["7e57f00d"]["live"] is True
    assert client.post("/api/session-ended", json={}).status_code == 422


def test_unknown_or_missing_sessions_are_errors_and_the_socket_stays_open(client):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "x1", "sessions.turns", {"session_id": "nope"})["code"] == "invalid_params"
        assert request(ws, "x2", "sessions.turns", {"session_id": "a8f68aa4"})["code"] == "invalid_params"
        assert request(ws, "x3", "sessions.turns", {"session_id": "dc88c6b4", "limit": 0})["code"] == "invalid_params"
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
