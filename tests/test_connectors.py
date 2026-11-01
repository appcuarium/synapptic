"""Connector settings: one generic value in config["settings"], sent under each provider's native name.

Driven through the real call_llm path. A local HTTP server answers as Ollama (/api/generate, streamed),
an OpenAI-compatible API (/chat/completions) or the Anthropic API (/v1/messages), and records each request
body; it is the only stand-in. The PreCompact throttle test runs the real `checkpoint` command with the
`claude` binary replaced, as in test_checkpoint.py.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner

from synapptic.cli import cli
from synapptic.providers import call_llm, load_config, save_config
from synapptic.state import save_archetype
from tests.test_checkpoint import SID, SLUG, append_turns, claude_spy, scratch, transcript  # noqa: F401


class ProviderApi(BaseHTTPRequestHandler):
    """Answers Ollama, OpenAI-compatible and Anthropic requests with "ok"; records (path, body)."""
    requests = []

    def do_POST(self):
        length = int(self.headers.get("content-length", 0))
        body = json.loads(self.rfile.read(length))
        ProviderApi.requests.append((self.path, body))
        if self.path.endswith("/api/generate"):
            payload = (json.dumps({"response": "ok", "done": True, "prompt_eval_count": 3, "eval_count": 1})
                       + "\n").encode()
        elif self.path.endswith("/chat/completions"):
            payload = json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode()
        else:
            payload = json.dumps({
                "id": "msg_test", "type": "message", "role": "assistant", "model": body["model"],
                "content": [{"type": "text", "text": "ok"}], "stop_reason": "end_turn", "stop_sequence": None,
                "usage": {"input_tokens": 3, "output_tokens": 1,
                          "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        return None


@pytest.fixture
def api(monkeypatch, tmp_path):
    ProviderApi.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), ProviderApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_address[1]}"
    # boundary: network - every provider is answered by the local server above
    monkeypatch.setenv("ANTHROPIC_BASE_URL", url)
    # boundary: filesystem - the config file lives under HOME
    monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "config.yaml")
    yield url
    server.shutdown()


def sent_body():
    return ProviderApi.requests[-1][1]


def test_legacy_ollama_settings_reach_ollama_under_its_native_names(api):
    save_config({"provider": "ollama", "model": "qwen3.8:27b-q4_K_M", "api_url": f"{api}/v1",
                 "ollama_temperature": 0.1, "ollama_num_ctx": 131072, "ollama_top_p": 0.95, "ollama_num_gpu": 40})

    assert call_llm("hi", config=load_config()) == "ok"

    options = sent_body()["options"]
    assert (options["temperature"], options["num_ctx"], options["top_p"], options["num_gpu"]) == (0.1, 131072, 0.95, 40)
    assert load_config()["settings"]["context_window"] == 131072 and "ollama_num_ctx" not in load_config()


def test_one_temperature_value_is_sent_as_each_providers_parameter(api):
    save_config({"provider": "openai", "model": "gpt-6.1-sol", "api_url": f"{api}/v1", "api_key": "test-key",
                 "settings": {"temperature": 0.3, "max_output_tokens": 8000}})

    assert call_llm("hi", config=load_config()) == "ok"

    assert (sent_body()["temperature"], sent_body()["max_tokens"]) == (0.3, 8000)


def test_a_setting_the_model_does_not_support_is_not_sent(api):
    save_config({"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "test-key",
                 "settings": {"temperature": 0.3, "max_output_tokens": 12000}})

    assert call_llm("hi", config=load_config()) == "ok"

    assert "temperature" not in sent_body()
    assert sent_body()["max_tokens"] == 12000


def test_config_show_lists_the_mapping_and_batch_availability(api):
    save_config({"provider": "ollama", "model": "qwen3.8:27b-q4_K_M", "settings": {"temperature": 0.1}})

    output = CliRunner().invoke(cli, ["config", "show"]).output

    assert "temperature        -> temperature  0.1 (set)" in output
    assert "context_window     -> num_ctx      65536 (default)" in output
    assert "Batches:         not available" in output


def test_precompact_flush_respects_the_synthesis_throttle(scratch, monkeypatch):
    save_archetype("## Guards\n\nwritten minutes ago", project_slug=SLUG)
    append_turns(transcript(scratch), [("Done.", "JULIET you skipped the container rule")])
    spy = claude_spy(monkeypatch, {1: ["Ran pytest on the host"]})

    result = CliRunner().invoke(cli, ["checkpoint", "-s", SID, "--flush"])

    assert result.exit_code == 0, result.output
    assert len(spy.prompts) == 1, "the flush re-synthesized an archetype written minutes ago"
