"""The dashboard websocket at /api/ws: protocol, config methods, pushes, origin check, and the SPA routes."""

import functools
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


def test_the_endpoint_url_is_a_setting_where_the_provider_takes_one(client, config_path):
    """The CLI's "API URL" question on the page (operator 2026-11-06): providers.list says which providers take a
    URL and their known one; config.update keeps it in the file, clears it on "", refuses one without http(s)."""
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        listed = {p["id"]: p for p in request(ws, "u0", "providers.list")["result"]}
        assert listed["custom"]["requires_url"] is True and listed["custom"]["default_url"] == ""
        assert listed["ollama"]["requires_url"] is False and listed["ollama"]["default_url"] == "http://localhost:11434/v1"
        assert listed["anthropic"]["requires_url"] is False and listed["anthropic"]["default_url"] == ""
        # made the default (2026-11-06, one entry per provider): the top level is the default's, so it shows here
        saved = request(ws, "u1", "config.update", {"provider": "custom", "model": "glm-5", "api_url": "http://10.0.0.7:8080/v1", "set_default": True})["result"]
        assert saved["provider"] == "custom" and saved["model"] == "glm-5" and saved["api_url"] == "http://10.0.0.7:8080/v1"
        assert request(ws, "u2", "config.update", {"api_url": "ftp://nope"})["code"] == "invalid_params"
        assert "api_url" not in request(ws, "u3", "config.update", {"api_url": ""})["result"]
    assert "api_url" not in yaml.safe_load(config_path.read_text())


def test_a_saved_custom_endpoint_becomes_a_card_of_its_own(client, config_path):
    """Saving the custom provider with a URL keeps the endpoint as an entry (operator 2026-11-06: a new card in
    the strip): named by the user or after its host, with its model; the same URL saved again updates the
    entry, another URL adds one; the entries survive a switch to another provider."""
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        # the view says per entry whether it holds a key of its own (2026-11-06, per-endpoint keys), never the key
        first = request(ws, "e1", "config.update", {"provider": "custom", "model": "glm-5", "api_url": "http://10.0.0.7:8080/v1", "endpoint_name": "the studio box"})["result"]
        # Anthropic stays the default (2026-11-06): the custom card's saves go to its own entry, so "default" is False
        assert first["endpoints"] == [{"name": "the studio box", "url": "http://10.0.0.7:8080/v1", "model": "glm-5", "api_key_set": False, "default": False}]
        again = request(ws, "e2", "config.update", {"provider": "custom", "model": "glm-5-air"})["result"]
        assert again["endpoints"] == [{"name": "the studio box", "url": "http://10.0.0.7:8080/v1", "model": "glm-5-air", "api_key_set": False, "default": False}]
        more = request(ws, "e3", "config.update", {"provider": "custom", "api_url": "https://llm.example.net/v1", "model": "qwen3"})["result"]
        assert [e["name"] for e in more["endpoints"]] == ["the studio box", "llm.example.net"]
        back = request(ws, "e4", "config.update", {"provider": "anthropic", "model": "claude-sonnet-5-5"})["result"]
        assert len(back["endpoints"]) == 2
    assert len(yaml.safe_load(config_path.read_text())["endpoints"]) == 2


def test_a_saved_custom_endpoint_can_be_removed(client, config_path):
    """Removing an endpoint by its URL drops its entry (operator 2026-11-06: its card goes) and only its own
    data (operator: "all info for the selected model, not the full file"): when it was the URL in use, its URL
    and model go, and the key in use too when it is the endpoint's own (typed with it); the other entries
    stay; an unknown URL changes nothing. The browser never sees an endpoint's key, only that it has one."""
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        # the custom card made the default (2026-11-06): what goes shows at the top level too
        request(ws, "r1", "config.update", {"provider": "custom", "model": "glm-5", "api_url": "http://10.0.0.7:8080/v1", "endpoint_name": "the studio box", "set_default": True})
        saved_with_key = request(ws, "r2", "config.update", {"provider": "custom", "api_url": "https://llm.example.net/v1", "model": "qwen3", "api_key": "sk-test-key"})["result"]
        assert [(e["name"], e["api_key_set"], "api_key" in e) for e in saved_with_key["endpoints"]] == [("the studio box", False, False), ("llm.example.net", True, False)]
        gone = request(ws, "r3", "config.update", {"remove_endpoint": "https://llm.example.net/v1"})["result"]
        assert [e["name"] for e in gone["endpoints"]] == ["the studio box"]
        assert gone["model"] == "" and not gone.get("api_url") and not gone["api_key_set"]
        same = request(ws, "r4", "config.update", {"remove_endpoint": "https://nowhere.example.net/v1"})["result"]
        assert [e["name"] for e in same["endpoints"]] == ["the studio box"]
    saved = yaml.safe_load(config_path.read_text())
    assert [e["name"] for e in saved["endpoints"]] == ["the studio box"] and "api_url" not in saved and "api_key" not in saved


def test_removing_an_endpoint_leaves_the_other_providers_settings_alone(client, config_path):
    """Operator 2026-11-06, after a custom endpoint's delete took the Anthropic key: a key saved with another
    provider is not the endpoint's, so removing the endpoint (even the one in use, even saved without a key
    of its own) leaves that key, and the provider comes back with it."""
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        anthropic = request(ws, "p1", "config.update", {"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-key"})["result"]
        assert anthropic["api_key_set"] and anthropic["api_key_hint"] == "-key"
        custom = request(ws, "p2", "config.update", {"provider": "custom", "model": "glm-5", "api_url": "http://10.0.0.7:8080/v1", "endpoint_name": "the studio box"})["result"]
        # Anthropic stays the default (2026-11-06): the endpoint lives in the custom card's own entry
        assert custom["endpoints"] == [{"name": "the studio box", "url": "http://10.0.0.7:8080/v1", "model": "glm-5", "api_key_set": False, "default": False}]
        assert custom["provider"] == "anthropic" and custom["providers"]["custom"]["api_url"] == "http://10.0.0.7:8080/v1"
        gone = request(ws, "p3", "config.update", {"remove_endpoint": "http://10.0.0.7:8080/v1"})["result"]
        assert gone["endpoints"] == [] and gone["providers"]["custom"] == {"model": "", "api_url": "", "api_key_set": False, "api_key_hint": ""}
        assert gone["provider"] == "anthropic" and gone["model"] == "claude-sonnet-5-5" and gone["api_key_set"] and gone["api_key_hint"] == "-key"
        back = request(ws, "p4", "config.update", {"provider": "anthropic", "model": "claude-sonnet-5-5"})["result"]
        assert back["api_key_set"] and back["api_key_hint"] == "-key"
    saved = yaml.safe_load(config_path.read_text())
    assert saved["api_key"] == "sk-ant-key" and saved["provider"] == "anthropic" and saved["model"] == "claude-sonnet-5-5"


def test_every_provider_keeps_its_own_settings_and_one_is_the_default(client, config_path):
    """Operator 2026-11-06 ("I should be able to configure multiple providers and be asked if I want to set it
    as default"): a card saved goes to its own entry and the default stays; with set_default the card becomes
    the default (the top level, what the CLI reads) and the previous default keeps its entry; the view lists
    every provider configured with its model and key hint, never a key."""
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        openai = request(ws, "d1", "config.update", {"provider": "openai", "model": "gpt-5", "api_key": "sk-openai-key"})["result"]
        assert openai["provider"] == "anthropic" and openai["model"] == "claude-sonnet-5-5" and openai["default_provider"] == "anthropic"
        assert openai["providers"]["openai"] == {"model": "gpt-5", "api_url": "", "api_key_set": True, "api_key_hint": "-key"}
        assert openai["providers"]["anthropic"]["model"] == "claude-sonnet-5-5" and "sk-openai-key" not in json.dumps(openai)
        moved = request(ws, "d2", "config.update", {"provider": "openai", "set_default": True})["result"]
        assert moved["provider"] == "openai" and moved["model"] == "gpt-5" and moved["api_key_hint"] == "-key"
        assert moved["providers"]["anthropic"]["model"] == "claude-sonnet-5-5" and moved["providers"]["anthropic"]["api_key_set"]
        back = request(ws, "d3", "config.update", {"provider": "anthropic", "set_default": True})["result"]
        assert back["provider"] == "anthropic" and back["model"] == "claude-sonnet-5-5" and back["api_key_hint"] == SECRET[-4:]
    saved = yaml.safe_load(config_path.read_text())
    assert saved["provider"] == "anthropic" and saved["api_key"] == SECRET
    assert saved["providers"]["openai"] == {"model": "gpt-5", "api_key": "sk-openai-key"}


def test_the_rule_keyword_is_read_and_written_with_the_rules_not_the_file(client, config_path, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))   # boundary: the rules' store lives under a scratch HOME, never the user's
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        assert request(ws, "k1", "config.get")["result"]["keyword"] == "RULE:"
        assert request(ws, "k2", "config.update", {"keyword": "GUARD->"})["result"]["keyword"] == "GUARD->"
        assert request(ws, "k3", "config.update", {"keyword": "two words"})["code"] == "invalid_params"
        assert request(ws, "k3b", "config.update", {"keyword": "GUARD!"})["code"] == "invalid_params"   # no special characters
        assert request(ws, "k4", "config.get")["result"]["keyword"] == "GUARD->"
    assert "keyword" not in yaml.safe_load(config_path.read_text())


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


def scope_stand_in(calls, rule_id, progress=None, provider=None, model=None, was_off=False, runs=3, scenario=None, temperature=None, **_):
    # boundary: llm and headless sessions - the one-rule benchmark (tests/test_benchmark_rule.py drives the real
    # one); records how the scope job called it and reports one step per rule
    calls.append((rule_id, provider, model, was_off, runs, scenario["scenario"] if scenario else None, temperature))
    progress({"kind": "note", "message": "answering the scenario"})
    return {"rule": rule_id, "classification": "effective" if not was_off else "redundant", "runs": runs, "fired": 1}


def scope_model(prompt, config=None, temperature=None, session_id=None):
    """The generation model (one scenario per asked rule, named after the rule's phrase), the judge on the
    control cases (right both times) and the runner on them (any answer)."""
    if "Generate exactly" in prompt:
        phrase = prompt.rsplit('"', 2)[-2]
        return json.dumps([{"rule": "r", "scenario": f"a scenario about {phrase}", "tension": "t", "category": "c"}])
    if "COMPLY" in prompt:
        return json.dumps({"verdict": "COMPLY" if "same language" in prompt else "VIOLATE", "reason": "ok"})
    return "Sure."


def test_the_benchmark_page_runs_every_rule_in_scope_and_keeps_the_run(client, monkeypatch, tmp_path):
    """guards.benchmark.scope (the Benchmark page, operator 2026-11-04, the previous CLI's full benchmark since
    2026-11-05): the rules under the chosen card selected by the seed, one scenario per rule generated once and
    kept for the seed, the two control cases read by the judge, then each rule through the one-rule benchmark on
    its kept scenario with the page's choices and its own switch to put back; every step reaches the topic under
    the rule's id (the run's own notes under the scope id), each rule ends with its verdict, the page's end
    carries the whole run, which is saved and listed newest first by guards.benchmark.runs and deleted by
    guards.benchmark.flush; a rerun with the seed asks the generation model nothing."""
    from synapptic import benchmark
    from synapptic.dashboard import handlers
    from synapptic.harness import add_rule, switch_rule

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(benchmark, "BENCHMARKS_DIR", tmp_path / "benchmarks")   # boundary: filesystem - the saved runs and scenarios
    add_rule("pytest-parallel", ["pytest"], "pytest runs with -n 4.", "")
    add_rule("commit-only-on-order", ["commit"], "Commit only when told.", "")
    add_rule("machine-only", ["docker"], "Tests run in containers.", "machine-be")   # under a directory: not Global
    switch_rule("commit-only-on-order", True)
    calls = []
    monkeypatch.setattr(handlers.benchmark, "run_rule_benchmark", functools.partial(scope_stand_in, calls))   # boundary: llm and headless sessions
    monkeypatch.setattr(benchmark, "call_llm", scope_model)   # boundary: llm - the generation model, the judge and the runner on the controls
    choices = {"project": "", "limit": 10, "runs": 1, "runner_provider": "ollama", "runner_model": "gemma3:27b", "seed": 3, "temperature": 0.4}
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        ws.send_text(json.dumps({"type": "subscribe", "topic": "benchmark"}))
        ws.send_text(json.dumps({"type": "subscribe", "topic": "guards"}))
        ws.send_text(json.dumps({"type": "ping"}))
        assert ws.receive_json() == {"type": "pong"}
        started = request(ws, "s1", "guards.benchmark.scope", choices)
        assert started["result"] == {"rule": "__scope__", "started": True, "running": True}
        frames = [ws.receive_json()]
        while frames[-1]["topic"] != "guards":
            frames.append(ws.receive_json())
        runs = request(ws, "s2", "guards.benchmark.runs")["result"]
        request(ws, "s5", "guards.benchmark.scope", choices)   # the rerun: the same seed, the kept scenarios
        while ws.receive_json()["topic"] != "guards":
            pass
        reruns = request(ws, "s6", "guards.benchmark.runs")["result"]
        state = request(ws, "s7", "guards.benchmark.state", {"id": "__scope__"})["result"]
        forgotten = request(ws, "s8", "guards.benchmark.flush_scenarios")["result"]   # the one kept set for seed 3
        flushed = request(ws, "s3", "guards.benchmark.flush")["result"]
        assert request(ws, "s4", "guards.benchmark.runs")["result"] == []
    # the Global card's rules only, in the seed's order, each on its kept scenario, with the choices and its own switch
    order = [rule["id"] for rule in benchmark.select_rules([{"id": "pytest-parallel"}, {"id": "commit-only-on-order"}], 10, 3)]
    expected = {"pytest-parallel": ("ollama", "gemma3:27b", False, 1, "a scenario about pytest", 0.4),
                "commit-only-on-order": ("ollama", "gemma3:27b", True, 1, "a scenario about commit", 0.4)}
    assert calls[:2] == [(rule_id, *expected[rule_id]) for rule_id in order] and calls[2:] == calls[:2]
    data = [f["data"] for f in frames if f["topic"] == "benchmark"]
    notes = [d["message"] for d in data if d["rule"] == "__scope__" and d.get("kind") == "note"]
    assert notes[:2] == [f"generating the scenario for {order[0]}", f"generating the scenario for {order[1]}"]
    assert "control comply: ok" in notes and "control violate: ok" in notes
    per_rule = [d["rule"] for d in data if d["rule"] != "__scope__"]
    assert per_rule == [order[0], order[0], order[1], order[1]]
    run = data[-1]["run"]
    assert data[-1]["done"] is True and run["rules"] == 2 and run["stopped"] is False and run["project"] == ""
    assert run["selected"] == order and run["controls"]["comply"] is True and run["controls"]["violate"] is True
    assert [v["classification"] for v in run["verdicts"]] == ["effective" if rule_id == "pytest-parallel" else "redundant" for rule_id in order]
    assert run["choices"]["runner_model"] == "gemma3:27b" and run["choices"]["seed"] == 3 and run["choices"]["temperature"] == 0.4
    assert len(runs) == 1 and runs[0]["verdicts"] == run["verdicts"] and runs[0]["id"]
    assert len(reruns) == 2 and flushed == {"flushed": 2} and forgotten == {"flushed": 1}
    # the rerun's state as a reloaded page reads it: the run's own notes once each (they were doubled, 2026-11-05)
    kept = [e["message"] for e in state["events"] if e["rule"] == "__scope__" and e.get("kind") == "note"]
    assert kept[:2] == ["reusing 2 cached scenario(s) for seed 3", "reading the judge on the two control cases"]
    assert len(kept) == len(set(kept))


def test_a_rule_says_where_it_came_from(client, monkeypatch, tmp_path):
    """The Benchmark page lists the knowledge under test with its origin (operator 2026-11-05): a rule written
    with `guards add` says hand; the store's own origins (synthesis, keyword) pass through as they are."""
    from synapptic.harness import add_rule

    monkeypatch.setenv("HOME", str(tmp_path))
    add_rule("pytest-parallel", ["pytest"], "pytest runs with -n 4.", "")
    with client.websocket_connect("/api/ws") as ws:
        ws.receive_json()
        rules = {rule["id"]: rule for rule in request(ws, "o1", "guards.list")["result"]["rules"]}
    assert rules["pytest-parallel"]["origin"] == "hand"


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
