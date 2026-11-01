"""The Anthropic API provider: real anthropic SDK, real `synapptic config provider`, against a local HTTP server.

The network is the only stand-in: a server on 127.0.0.1 answers as the Messages API would, and the SDK is
pointed at it through ANTHROPIC_BASE_URL. Each request body it receives is recorded.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner

from synapptic import providers
from synapptic.cli import cli
from synapptic.providers import PROVIDERS, call_anthropic, load_config

CURRENT_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-5-5"}
RETIRED_MODELS = {"claude-sonnet-4-20250514", "claude-opus-4-20250805"}


def message_body(text, model="claude-sonnet-5-5", stop_reason="end_turn"):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": model,
            "content": [{"type": "text", "text": text}], "stop_reason": stop_reason, "stop_sequence": None,
            "usage": {"input_tokens": 12, "output_tokens": 3,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


class MessagesApi(BaseHTTPRequestHandler):
    """Answers POST /v1/messages with the class's (status, body); records every request body."""
    requests = []
    status, body = 200, message_body("ok")

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        MessagesApi.requests.append({"path": self.path, "body": json.loads(self.rfile.read(length))})
        payload = json.dumps(MessagesApi.body).encode()
        self.send_response(MessagesApi.status)
        self.send_header("content-type", "application/json")
        self.send_header("retry-after", "0")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return None


@pytest.fixture
def api(monkeypatch):
    MessagesApi.requests, MessagesApi.status, MessagesApi.body = [], 200, message_body("ok")
    server = ThreadingHTTPServer(("127.0.0.1", 0), MessagesApi)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    # boundary: network - the Anthropic API is answered by the local server above
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    yield MessagesApi
    server.shutdown()


def test_setup_offers_only_current_claude_models():
    offered = {model_id for model_id, _ in PROVIDERS["anthropic"]["models"]}
    assert CURRENT_MODELS <= offered
    assert not offered & RETIRED_MODELS
    assert PROVIDERS["anthropic"]["default_model"] in CURRENT_MODELS


@pytest.mark.parametrize("saved, expected", [
    ("claude-sonnet-4-20250514", "claude-sonnet-5-5"),  # retired: the provider default
    ("claude-haiku-4-5-20251001", "claude-haiku-4-5"),  # renamed: carried over
    ("claude-opus-5-5", "claude-opus-5-5"),  # still offered: untouched
])
def test_saved_model_is_updated_when_the_config_is_loaded(tmp_path, monkeypatch, saved, expected):
    # boundary: filesystem - the config file lives under HOME
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
    providers.save_config({"provider": "anthropic", "model": saved, "api_key": "test-key"})

    assert load_config()["model"] == expected
    assert load_config()["model"] == expected  # saved, so the next load reads the updated model


def test_custom_model_not_in_the_menu_is_kept(tmp_path, monkeypatch):
    # boundary: filesystem - the config file lives under HOME
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
    providers.save_config({"provider": "ollama", "model": "my-finetune:latest"})

    assert load_config()["model"] == "my-finetune:latest"


def test_setup_keeps_the_saved_model_preselected_while_it_is_offered(api, tmp_path, monkeypatch):
    # boundary: filesystem - the config file lives under HOME
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
    providers.save_config({"provider": "anthropic", "model": "claude-opus-5-5", "api_key": "test-key"})
    choice = list(PROVIDERS).index("anthropic") + 1

    result = CliRunner().invoke(cli, ["config", "provider"], input=f"{choice}\n\n\n\n")

    assert result.exit_code == 0, result.output
    assert load_config()["model"] == "claude-opus-5-5"


def test_setup_preselects_the_default_when_the_saved_model_was_retired(api, tmp_path, monkeypatch):
    # boundary: filesystem - the config file lives under HOME
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
    providers.save_config({"provider": "anthropic", "model": "claude-sonnet-4-20250514", "api_key": "test-key"})
    choice = list(PROVIDERS).index("anthropic") + 1

    result = CliRunner().invoke(cli, ["config", "provider"], input=f"{choice}\n\n\n\n")

    assert result.exit_code == 0, result.output
    assert load_config()["model"] == PROVIDERS["anthropic"]["default_model"]


def test_call_sends_a_current_request_and_returns_the_text(api):
    reply = call_anthropic("Extract observations", "claude-sonnet-5-5", "test-key", temperature=0.3)

    assert reply == "ok"
    sent = api.requests[0]
    assert sent["path"] == "/v1/messages"
    assert sent["body"]["model"] == "claude-sonnet-5-5"
    assert sent["body"]["max_tokens"] == 16_000
    assert "temperature" not in sent["body"]


def test_rate_limit_is_reported_for_call_llm_to_retry(api):
    api.status, api.body = 429, {"type": "error", "error": {"type": "rate_limit_error", "message": "slow down"}}

    reply = call_anthropic("Extract observations", "claude-sonnet-5-5", "test-key")

    assert reply is None
    assert providers._last_http_status == 429


def test_refusal_returns_nothing(api):
    api.body = message_body("[partial text written before the refusal]", stop_reason="refusal")
    assert call_anthropic("Extract observations", "claude-sonnet-5-5", "test-key") is None


def test_setup_selects_the_api_and_tests_the_connection(api, tmp_path, monkeypatch):
    # boundary: filesystem - the config file lives under HOME
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
    choice = list(PROVIDERS).index("anthropic") + 1

    result = CliRunner().invoke(cli, ["config", "provider"], input=f"{choice}\n1\ntest-key\n\n")

    assert result.exit_code == 0, result.output
    saved = load_config()
    assert (saved["provider"], saved["model"], saved["api_key"]) == ("anthropic", "claude-sonnet-5-5", "test-key")
    assert "Connection OK" in result.output
    assert api.requests and api.requests[0]["body"]["model"] == "claude-sonnet-5-5"
