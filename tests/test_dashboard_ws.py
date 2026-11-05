"""The dashboard websocket at /api/ws: protocol, config methods, pushes, origin check, and the SPA routes."""

import json
import sys
import time

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from synapptic.dashboard.app import create_app
from synapptic.dashboard.handlers import change_view

SECRET = "sk-ant-api03-secret-value-WXYZ"


@pytest.fixture
def config_path(tmp_path, monkeypatch):
    path = tmp_path / ".synapptic" / "config.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(yaml.safe_dump({"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": SECRET}))
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", path)
    return path


@pytest.fixture
def client(config_path, tmp_path):
    with TestClient(create_app(static_dir=tmp_path / "no-build")) as test_client:
        yield test_client


def request(ws, request_id, method, params=None):
    ws.send_text(json.dumps({"type": "request", "id": request_id, "method": method, "params": params or {}}))
    return ws.receive_json()


def test_first_frame_is_hello_with_the_protocol_version(client):
    with client.websocket_connect("/api/ws") as ws:
        assert ws.receive_json() == {"type": "hello", "protocol": 1, "server": "0.1.0b6"}


def test_config_get_never_sends_the_api_key(client):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        reply = request(ws, "r1", "config.get")
    assert reply["type"] == "response" and reply["id"] == "r1"
    assert reply["result"]["provider"] == "anthropic"
    assert reply["result"]["api_key_set"] is True and reply["result"]["api_key_hint"] == "WXYZ"
    assert SECRET not in json.dumps(reply)


def test_config_update_saves_the_file_and_pushes_to_subscribers(client, config_path):
    with client.websocket_connect("/api/ws") as editor, client.websocket_connect("/api/ws") as watcher:
        editor.receive_json()
        watcher.receive_json()
        watcher.send_text(json.dumps({"type": "subscribe", "topic": "config"}))
        watcher.send_text(json.dumps({"type": "ping"}))
        assert watcher.receive_json() == {"type": "pong"}  # frames are handled in order: subscribed
        reply = request(editor, "r2", "config.update", {"model": "claude-opus-5-5", "batches": {"extract": True}})
        pushed = watcher.receive_json()
    saved = yaml.safe_load(config_path.read_text())
    assert saved["model"] == "claude-opus-5-5" and saved["batches"] == {"extract": True} and saved["api_key"] == SECRET
    assert reply["result"]["model"] == "claude-opus-5-5"
    assert pushed["type"] == "event" and pushed["topic"] == "config" and pushed["data"]["model"] == "claude-opus-5-5"
    assert SECRET not in json.dumps(pushed)


def test_bad_requests_get_errors_and_the_socket_stays_open(client):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "r3", "config.update", {"provider": "nope"})["code"] == "invalid_params"
        assert request(ws, "r4", "no.such.method")["code"] == "unknown_method"
        ws.send_text("{not json")
        assert ws.receive_json()["code"] == "bad_message"
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}


def test_the_tuning_dial_is_read_and_written_through_config(client, config_path):
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "r5", "config.get")["result"]["tuning"] == "normal"   # the default, said explicitly
        reply = request(ws, "r6", "config.update", {"tuning": "eager"})
        assert reply["result"]["tuning"] == "eager"
        assert request(ws, "r7", "config.update", {"tuning": "wild"})["code"] == "invalid_params"
    saved = yaml.safe_load(config_path.read_text())
    assert saved["harness"] == {"tuning": "eager"} and saved["api_key"] == SECRET


def test_the_guards_methods_say_when_the_core_is_missing(client, monkeypatch):
    # boundary: the compiled core is a separately installed package; here it is absent
    monkeypatch.setattr("synapptic.core.core", None)
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        for method, params in (("guards.list", {}), ("guards.numbers", {}), ("guards.changes", {}),
                               ("guards.switch", {"id": "r", "off": True}), ("guards.undo", {"id": "v1"})):
            reply = request(ws, "r8", method, params)
            assert reply["code"] == "invalid_params" and "synapptic-core package is not installed" in reply["message"], method
        assert request(ws, "r9", "guards.switch", {"id": "r"})["code"] == "invalid_params"   # off is required


def test_the_profile_is_read_per_project_and_global(client, tmp_path, monkeypatch):
    # boundary: the profile directories are this test's own, never the user's ~/.synapptic
    monkeypatch.setattr("synapptic.state.GLOBAL_DIR", tmp_path / "global")
    monkeypatch.setattr("synapptic.state.PROJECTS_DIR", tmp_path / "projects")
    (tmp_path / "global").mkdir()
    (tmp_path / "global" / "archetype.md").write_text("# User Archetype\n\nReads first, verifies everything.\n\n## Guards\n\n1. NEVER guess.\n")
    (tmp_path / "global" / "profile.yaml").write_text("metadata:\n  profile_version: 3\ndimensions:\n  values:\n  - {text: correctness}\n  - {text: speed}\n")
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        found = request(ws, "p1", "profile.get", {"project": ""})["result"]
        missing = request(ws, "p2", "profile.get", {"project": "no-such-project"})["result"]
        assert request(ws, "p3", "profile.get", {"project": "x", "extra": 1})["code"] == "invalid_params"
    assert found["project"] == "" and found["version"] == 3 and "Reads first" in found["archetype"]
    assert "## Guards" not in found["archetype"]   # the twin only: the guards are the rules the page lists
    assert found["dimensions"] == [{"name": "values", "count": 2}]
    assert missing == {"project": "no-such-project", "archetype": "", "dimensions": [], "version": 0}


def test_a_change_is_shown_as_counts_never_as_the_words():
    change = {"id": "v1", "ts": 1.0, "rule_id": "r", "kind": "promoted", "before": "a|b", "after": "a|b|c\\|d|(e|f)"}
    shown = change_view(change)
    assert shown["added"] == 2 and shown["removed"] == 0
    assert shown["kind"] == "promoted" and "before" not in shown and "after" not in shown
    assert change_view({**change, "before": "a|b|c", "after": "a"})["removed"] == 2


def test_the_restart_button_spawns_the_servers_own_restart_command(client, tmp_path, monkeypatch):
    from synapptic.dashboard import handlers

    marker = tmp_path / "restarted"
    # boundary: process - the server's restart command (`synapptic dashboard restart`) would stop this very
    # process; a command that leaves a marker stands in for it, spawned the same detached way
    monkeypatch.setitem(handlers.RESTART, "command", [sys.executable, "-c", f"open({str(marker)!r}, 'w').write('x')"])
    monkeypatch.setitem(handlers.RESTART, "log", str(tmp_path / "log"))
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "s1", "server.restart")["result"] == {"restarting": True}
        deadline = time.time() + 5
        while time.time() < deadline and not marker.exists():
            time.sleep(0.1)
        assert marker.exists()
        monkeypatch.setitem(handlers.RESTART, "command", None)
        reply = request(ws, "s2", "server.restart")
    assert reply["code"] == "invalid_params" and "restart it where it runs" in reply["message"]


def benchmark_stand_in(rule_id, config=None, judge_config=None, runs=3, cwd=None, progress=None, model=None, **_):
    # boundary: llm and headless sessions - the one-rule benchmark itself (tests/test_benchmark_rule.py drives
    # the real one); this one reports a stage, a reply and a judge verdict, and answers a verdict
    progress({"kind": "note", "message": "generating the scenario"})
    progress({"kind": "reply", "run": 1, "arm": "without", "session": "s-off", "fired": False, "text": "Run pytest."})
    progress({"kind": "judge", "run": 1, "arm": "without", "score": "FAIL", "reason": "serial"})
    return {"rule": rule_id, "classification": "effective", "runs": runs, "fired": 1,
            "judge": judge_config.get("model"), "runner": model}


def test_the_benchmark_button_runs_the_rule_in_the_background_and_pushes_its_steps(client, monkeypatch):
    """guards.benchmark starts the one-rule benchmark as a job: the reply comes at once, every step reaches the
    benchmark topic, the end carries the verdict, and the rules list is pushed again."""
    from synapptic.dashboard import handlers

    monkeypatch.setattr(handlers.benchmark, "run_rule_benchmark", benchmark_stand_in)   # boundary: llm and headless sessions
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "subscribe", "topic": "benchmark"}))
        ws.send_text(json.dumps({"type": "subscribe", "topic": "guards"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
        started = request(ws, "b1", "guards.benchmark", {"id": "pytest-parallel", "runs": 1, "runner_model": "opus", "judge_provider": "anthropic", "judge_model": "claude-opus-5-5"})
        assert started["result"] == {"rule": "pytest-parallel", "started": True, "running": True}
        # the job always ends with the rules list pushed: read up to it
        frames = [ws.receive_json()]
        while frames[-1]["topic"] != "guards":
            frames.append(ws.receive_json())
    assert [f["topic"] for f in frames] == ["benchmark"] * 4 + ["guards"]
    assert frames[0]["data"] == {"rule": "pytest-parallel", "kind": "note", "message": "generating the scenario"}
    assert frames[1]["data"]["kind"] == "reply" and frames[1]["data"]["text"] == "Run pytest." and frames[1]["data"]["arm"] == "without"
    assert frames[2]["data"]["kind"] == "judge" and frames[2]["data"]["score"] == "FAIL"
    verdict = frames[3]["data"]["verdict"]
    assert frames[3]["data"]["done"] is True and verdict["classification"] == "effective" and verdict["runs"] == 1
    # the page's choices reach the run: the judge's model and the headless session's model
    assert verdict["judge"] == "claude-opus-5-5" and verdict["runner"] == "opus"
    assert "rules" in frames[4]["data"]


def test_stopping_a_benchmark_sets_its_cancel_and_answers_whether_one_ran(client, monkeypatch):
    """guards.benchmark.stop flags the rule's running job (the run ends at its next arm); with no job running
    it says so. The job's cancel is created with the job and cleared with it."""
    from synapptic.dashboard import handlers

    monkeypatch.setattr(handlers.benchmark, "run_rule_benchmark", benchmark_stand_in)   # boundary: llm and headless sessions
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "s0", "guards.benchmark.stop", {"id": "pytest-parallel"})["result"] == {"rule": "pytest-parallel", "stopped": False, "running": False}
        ws.send_text(json.dumps({"type": "subscribe", "topic": "guards"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
        request(ws, "b1", "guards.benchmark", {"id": "pytest-parallel", "runs": 1})
        assert isinstance(handlers.benchmark_cancels.get("pytest-parallel"), handlers.threading.Event)
        while ws.receive_json()["topic"] != "guards":
            pass   # the job ends with the rules list pushed
    assert "pytest-parallel" not in handlers.benchmark_cancels and "pytest-parallel" not in handlers.benchmark_jobs


def benchmark_provider_stand_in(rule_id, progress=None, provider=None, model=None, **_):
    # boundary: llm and headless sessions - answers which runner the page's choices reached
    return {"rule": rule_id, "classification": "effective", "runs": 1, "fired": 1, "runner": [provider, model]}


def test_the_runner_provider_chosen_on_the_page_reaches_the_run(client, monkeypatch):
    """The page's runner is a provider and a model (operator 2026-11-05: any provider, Ollama included): both
    reach the one-rule benchmark."""
    from synapptic.dashboard import handlers

    monkeypatch.setattr(handlers.benchmark, "run_rule_benchmark", benchmark_provider_stand_in)   # boundary: llm and headless sessions - the one-rule benchmark (tests/test_benchmark_rule.py drives the real one)
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "subscribe", "topic": "benchmark"}))
        ws.send_text(json.dumps({"type": "subscribe", "topic": "guards"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
        request(ws, "b1", "guards.benchmark", {"id": "pytest-parallel", "runs": 1, "runner_provider": "ollama", "runner_model": "gemma3:27b"})
        frames = [ws.receive_json()]
        while frames[-1]["topic"] != "guards":
            frames.append(ws.receive_json())
    assert frames[0]["data"]["done"] is True and frames[0]["data"]["verdict"]["runner"] == ["ollama", "gemma3:27b"]


def test_a_restart_is_refused_while_a_benchmark_runs(client, monkeypatch):
    """A restart kills the benchmark jobs with the server (an opus run was lost that way, 2026-11-05): the
    page's restart button is refused while one runs, and guards.benchmark.running names the rules."""
    from synapptic.dashboard import handlers

    monkeypatch.setitem(handlers.RESTART, "command", [sys.executable, "-c", "pass"])
    monkeypatch.setitem(handlers.benchmark_jobs, "plain-trading-terms", object())   # a job still running
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "r1", "guards.benchmark.running")["result"] == {"rules": ["plain-trading-terms"]}
        refused = request(ws, "r2", "server.restart")
    assert refused["code"] == "invalid_params" and "a benchmark is running on plain-trading-terms" in refused["message"]


def test_a_page_opened_mid_run_gets_the_run_so_far(client, monkeypatch):
    """guards.benchmark.state answers the run's events so far and whether it still runs: a reload mid-run shows
    the run instead of nothing (operator 2026-11-05). The events stay after the end until the next run."""
    from synapptic.dashboard import handlers

    monkeypatch.setattr(handlers.benchmark, "run_rule_benchmark", benchmark_stand_in)   # boundary: llm and headless sessions
    monkeypatch.setattr(handlers, "benchmark_events", {})   # this test's own memory of runs (the module keeps the last one)
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "st0", "guards.benchmark.state", {"id": "pytest-parallel"})["result"] == {"rule": "pytest-parallel", "running": False, "events": []}
        ws.send_text(json.dumps({"type": "subscribe", "topic": "guards"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
        request(ws, "b1", "guards.benchmark", {"id": "pytest-parallel", "runs": 1})
        while ws.receive_json()["topic"] != "guards":
            pass
        state = request(ws, "st1", "guards.benchmark.state", {"id": "pytest-parallel"})["result"]
    assert state["running"] is False and [e["kind"] for e in state["events"]] == ["note", "reply", "judge"]
    assert state["events"][1]["text"] == "Run pytest." and state["events"][0]["rule"] == "pytest-parallel"


def test_a_socket_from_another_website_is_refused(client):
    with pytest.raises(WebSocketDisconnect) as refused:
        with client.websocket_connect("/api/ws", headers={"origin": "http://evil.example"}) as ws:
            ws.receive_json()
    assert refused.value.code == 1008


def test_client_side_routes_get_the_built_index(config_path, tmp_path):
    static_dir = tmp_path / "static"
    (static_dir / "assets").mkdir(parents=True)
    (static_dir / "index.html").write_text("<!doctype html><title>Synapptic</title>")
    (static_dir / "assets" / "app.js").write_text("console.log(1)")
    with TestClient(create_app(static_dir=static_dir)) as client:
        assert client.get("/api/health").json() == {"status": "ok", "protocol": 1}
        assert "Synapptic" in client.get("/sessions/appcuarium-machine").text
        assert client.get("/assets/app.js").text == "console.log(1)"
