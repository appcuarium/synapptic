"""OpenAI relay route stores a string session id; CLI reads the same database file the relay writes.

Real router, real forwarder, real SessionTracker, real MetricsStore on a tmp SQLite file. Only the upstream
HTTP API is stubbed (httpx MockTransport), because it is a network service outside the project.
"""

import httpx
from click.testing import CliRunner
from fastapi import FastAPI
from fastapi.testclient import TestClient

from synapptic.cli import cli
from synapptic.relay.routers import openai as openai_router
from synapptic.relay.store import MetricsStore


def upstream_openai(request):
    return httpx.Response(200, json={"choices": [{"message": {"content": "hi"}}], "usage": {}})


def test_openai_route_upserts_string_session_id(tmp_path):
    store = MetricsStore(tmp_path / "proxy.db")
    store.init_db()
    app = FastAPI()
    app.include_router(openai_router.router, prefix="/v1")
    app.state.metrics_store = store
    # boundary: network - api.openai.com is answered in-process instead of over the wire
    app.state.http_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream_openai))

    response = TestClient(app, raise_server_exceptions=False).post(
        "/v1/chat/completions", json={"model": "gpt-4o", "messages": [{"role": "user", "content": "hi"}]})

    assert response.status_code == 200
    sessions = store.get_all_sessions()
    assert sessions and isinstance(sessions[0]["id"], str)


def test_relay_status_reads_the_relay_database(tmp_path, monkeypatch):
    db_path = tmp_path / ".synapptic" / "proxy.db"
    db_path.parent.mkdir()
    store = MetricsStore(db_path)
    store.init_db()
    store.upsert_session("s1", "fp")
    store.insert_request("s1", "openai", "gpt-4o", 7, 3, 0, 0, 1, 0, 0, 1.0, False)
    store.close()
    # boundary: env/filesystem - HOME and the paths derived from it at import time point at tmp_path
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr("synapptic.relay.config.DB_PATH", db_path)
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / ".synapptic" / "config.yaml")

    result = CliRunner().invoke(cli, ["relay", "status"])

    assert result.exit_code == 0, result.output
    assert "Requests: 1" in result.output
