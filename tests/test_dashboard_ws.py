"""The dashboard websocket at /api/ws: protocol, config methods, pushes, origin check, and the SPA routes."""

import json

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from synapptic.dashboard.app import create_app

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
