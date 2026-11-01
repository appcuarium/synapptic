"""Message Batches in checkpoint: a user setting, used only where the connector says the provider and model
support batches; submitted chunks are collected at a later checkpoint.

Real `checkpoint`, `config batches` and `config show` commands, real anthropic SDK. A local HTTP server answers
as the Claude API (Batches endpoints and /v1/messages) and records each request; it is the only stand-in.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from click.testing import CliRunner

from synapptic.cli import cli
from synapptic.providers import CONNECTORS, batches_enabled, load_config, save_config
from synapptic.state import load_pending_batches, load_profile, load_retry_chunks
from tests.test_checkpoint import SID, SLUG, append_turns, claude_spy, scratch, transcript  # noqa: F401

OBSERVATIONS = json.dumps([{"dimension": "ai_failures", "observation": "Ran pytest on the host",
                            "confidence": 0.8, "evidence": "user said so"}])


def message(text):
    return {"id": "msg_test", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5,
                      "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0}}


class ClaudeApi(BaseHTTPRequestHandler):
    """Batches: POST creates (records the requests), GET reports in_progress until `ended`, then serves results
    (`outcome` per custom_id: "succeeded" or "errored"). POST /v1/messages answers a direct call."""
    batches, direct_calls, ended, outcome = {}, [], False, "succeeded"

    def reply(self, status, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", content_type)
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def batch(self, batch_id):
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        return {"id": batch_id, "type": "message_batch",
                "processing_status": "ended" if ClaudeApi.ended else "in_progress",
                "request_counts": {"processing": 0, "succeeded": 1, "errored": 0, "canceled": 0, "expired": 0},
                "created_at": "2026-11-01T00:00:00Z", "expires_at": "2026-11-02T00:00:00Z",
                "ended_at": "2026-11-01T00:10:00Z" if ClaudeApi.ended else None,
                "archived_at": None, "cancel_initiated_at": None,
                "results_url": f"{base}/v1/messages/batches/{batch_id}/results" if ClaudeApi.ended else None}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))))
        if self.path.startswith("/v1/messages/batches"):
            batch_id = f"msgbatch_test{len(ClaudeApi.batches) + 1}"
            ClaudeApi.batches[batch_id] = body["requests"]
            return self.reply(200, self.batch(batch_id))
        ClaudeApi.direct_calls.append(body)
        return self.reply(200, message(OBSERVATIONS))

    def do_GET(self):
        parts = self.path.strip("/").split("/")  # v1 messages batches <id> [results]
        batch_id = parts[3]
        if parts[-1] != "results":
            return self.reply(200, self.batch(batch_id))
        lines = []
        for request in ClaudeApi.batches[batch_id]:
            if ClaudeApi.outcome == "succeeded":
                result = {"type": "succeeded", "message": message(OBSERVATIONS)}
            else:
                result = {"type": "errored", "error": {"type": "error",
                                                       "error": {"type": "api_error", "message": "overloaded"}}}
            lines.append(json.dumps({"custom_id": request["custom_id"], "result": result}))
        return self.reply(200, ("\n".join(lines) + "\n").encode(), "application/binary")

    def log_message(self, *args):
        return None


@pytest.fixture
def api(scratch, monkeypatch):
    ClaudeApi.batches, ClaudeApi.direct_calls, ClaudeApi.ended, ClaudeApi.outcome = {}, [], False, "succeeded"
    server = ThreadingHTTPServer(("127.0.0.1", 0), ClaudeApi)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    # boundary: network - the Claude API is answered by the local server above
    monkeypatch.setenv("ANTHROPIC_BASE_URL", f"http://127.0.0.1:{server.server_address[1]}")
    # boundary: filesystem - pending batches live under HOME; each test gets its own directory
    monkeypatch.setattr("synapptic.state.BATCHES_DIR", scratch / "synapptic" / "batches")
    save_config({"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "test-key"})
    yield ClaudeApi
    server.shutdown()


def turn_batching(on):
    config = load_config()
    config["batches"] = {"extract": on}
    save_config(config)


def checkpoint():
    result = CliRunner().invoke(cli, ["checkpoint", "-s", SID, "--flush", "--synthesize-after", "-1"])
    assert result.exit_code == 0, result.output
    return result.output


def test_batching_is_off_until_the_user_turns_it_on(api, scratch):
    append_turns(transcript(scratch), [("Plan.", "KILO no, read it first")])

    checkpoint()

    assert api.batches == {} and len(api.direct_calls) == 1
    assert not batches_enabled(load_config(), "extract")


def test_batching_on_submits_the_chunk_and_moves_the_cursor(api, scratch):
    turn_batching(True)
    append_turns(transcript(scratch), [("Plan.", "LIMA you skipped the test")])

    output = checkpoint()

    assert api.direct_calls == []
    [requests] = api.batches.values()
    assert "LIMA" in requests[0]["params"]["messages"][0]["content"]
    assert requests[0]["params"]["model"] == "claude-sonnet-5-5"
    assert [r["batch_id"] for r in load_pending_batches()] == ["msgbatch_test1"]
    assert "submitted as batch msgbatch_test1" in output


def test_a_later_checkpoint_collects_the_batch_and_rebuilds_the_profile(api, scratch):
    turn_batching(True)
    append_turns(transcript(scratch), [("Plan.", "MIKE you skipped the test")])
    checkpoint()
    api.ended = True

    checkpoint()

    assert load_pending_batches() == []
    prefs = load_profile(project_slug=SLUG)["dimensions"]["ai_failures"]
    assert [p["observation"] for p in prefs] == ["Ran pytest on the host"]


def test_a_failed_chunk_is_resubmitted_with_the_next_batch(api, scratch):
    turn_batching(True)
    append_turns(transcript(scratch), [("Plan.", "NOVEMBER you skipped the test")])
    checkpoint()
    api.ended, api.outcome = True, "errored"
    append_turns(transcript(scratch), [("Done.", "OSCAR still wrong")])

    checkpoint()

    second = api.batches["msgbatch_test2"]
    prompts = " ".join(r["params"]["messages"][0]["content"] for r in second)
    assert "NOVEMBER" in prompts and "OSCAR" in prompts
    assert load_retry_chunks() == []


def test_a_submitted_batch_is_collected_after_batching_is_turned_off(api, scratch):
    turn_batching(True)
    append_turns(transcript(scratch), [("Plan.", "PAPA you skipped the test")])
    checkpoint()
    turn_batching(False)
    api.ended = True

    checkpoint()

    assert load_pending_batches() == []


def test_a_pending_batch_without_an_api_key_waits_instead_of_breaking_the_checkpoint(api, scratch, monkeypatch):
    turn_batching(True)
    append_turns(transcript(scratch), [("Plan.", "QUEBEC you skipped the test")])
    checkpoint()
    # boundary: env - no ANTHROPIC_API_KEY in the environment either
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    save_config({"provider": "claude-cli", "model": "sonnet"})  # switched back, no API key configured
    claude_spy(monkeypatch)
    append_turns(transcript(scratch), [("Done.", "ROMEO more")])

    checkpoint()

    assert [r["batch_id"] for r in load_pending_batches()] == ["msgbatch_test1"]


def test_the_setting_written_by_the_cli_is_the_one_every_reader_sees(api):
    result = CliRunner().invoke(cli, ["config", "batches"], input="y\n")

    assert result.exit_code == 0, result.output
    assert load_config()["batches"] == {"extract": True}
    assert "Batches:         extract on" in CliRunner().invoke(cli, ["config", "show"]).output


def test_batching_is_not_offered_where_the_provider_or_model_lacks_it(api, monkeypatch):
    save_config({"provider": "claude-cli", "model": "sonnet", "batches": {"extract": True}})
    assert not batches_enabled(load_config(), "extract")
    assert "not available" in CliRunner().invoke(cli, ["config", "batches"]).output

    # a model without batch support, declared in the connector
    monkeypatch.setitem(CONNECTORS["anthropic"], "model_capabilities", {"claude-haiku-4-5": {"batches": False}})
    save_config({"provider": "anthropic", "model": "claude-haiku-4-5", "batches": {"extract": True}})
    assert not batches_enabled(load_config(), "extract")
