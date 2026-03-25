"""Tests for synapptic.providers — config, token formatting, call tracking."""

import os
import stat
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from synapptic.providers import (
    format_tokens,
    get_call_totals,
    load_config,
    reset_call_totals,
    save_config,
    track_call,
)


class TestLoadConfig:

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", tmp_path / "nonexistent.yaml")
        assert load_config() == {}

    def test_malformed_yaml_returns_empty(self, tmp_path, monkeypatch):
        bad = tmp_path / "bad.yaml"
        bad.write_text(": invalid: yaml: [")
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", bad)
        assert load_config() == {}


class TestSaveConfig:

    def test_roundtrip(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", config_path)
        data = {"provider": "groq", "model": "llama-3.1-8b-instant", "api_key": "sk-test123"}
        save_config(data)
        loaded = load_config()
        assert loaded == data

    def test_permissions_0600(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        monkeypatch.setattr("synapptic.providers.CONFIG_PATH", config_path)
        save_config({"provider": "test"})
        mode = config_path.stat().st_mode
        assert stat.S_IMODE(mode) == 0o600


class TestFormatTokens:

    def test_below_1000(self):
        assert format_tokens(500) == "500"

    def test_1234(self):
        assert format_tokens(1234) == "1.2k"

    def test_15000(self):
        assert format_tokens(15000) == "15.0k"

    def test_zero(self):
        assert format_tokens(0) == "0"


class TestCallTracking:

    def setup_method(self):
        reset_call_totals()

    def test_track_increments(self):
        track_call({"prompt_tokens": 100, "response_tokens": 50, "total_tokens": 150, "elapsed_sec": 1.5})
        totals = get_call_totals()
        assert totals["calls"] == 1
        assert totals["prompt_tokens"] == 100
        assert totals["response_tokens"] == 50

    def test_multiple_calls_accumulate(self):
        track_call({"prompt_tokens": 100, "response_tokens": 50, "total_tokens": 150})
        track_call({"prompt_tokens": 200, "response_tokens": 100, "total_tokens": 300})
        totals = get_call_totals()
        assert totals["calls"] == 2
        assert totals["prompt_tokens"] == 300

    def test_reset_zeroes(self):
        track_call({"prompt_tokens": 100, "response_tokens": 50, "total_tokens": 150})
        reset_call_totals()
        totals = get_call_totals()
        assert totals["calls"] == 0
        assert totals["prompt_tokens"] == 0

    def test_get_totals_returns_copy(self):
        totals1 = get_call_totals()
        totals1["calls"] = 999
        totals2 = get_call_totals()
        assert totals2["calls"] == 0


class TestCallLlmNoProvider:

    def test_returns_none_without_provider(self):
        from synapptic.providers import call_llm
        result = call_llm("test prompt", config={})
        assert result is None


class TestRedactSecrets:

    def test_redacts_sk_key(self):
        from synapptic.providers import redact_secrets
        result = redact_secrets("error with sk-ant-v7abcdef1234567890")
        assert "sk-ant-v7..." in result
        assert "1234567890" not in result

    def test_redacts_aiza_key(self):
        from synapptic.providers import redact_secrets
        result = redact_secrets("AIzaSyAbcdef1234567890XXXX error")
        assert "AIzaSy..." in result
        assert "1234567890" not in result

    def test_redacts_bearer_token(self):
        from synapptic.providers import redact_secrets
        result = redact_secrets("Authorization: Bearer abcd12345678")
        assert "Bearer abcd..." in result
        assert "12345678" not in result

    def test_no_change_on_safe_text(self):
        from synapptic.providers import redact_secrets
        text = "Normal error message with no keys"
        assert redact_secrets(text) == text

    def test_redacts_gsk_key(self):
        from synapptic.providers import redact_secrets
        result = redact_secrets("gsk_abcd_EFGH_secret12345")
        assert "gsk_abcd..." in result
        assert "secret12345" not in result


class TestHttpBlocking:

    def test_non_localhost_http_returns_none(self):
        from synapptic.providers import call_openai_compatible
        result = call_openai_compatible(
            prompt="test",
            model="gpt-4",
            api_url="http://remote-server.example.com/v1",
            api_key="sk-test1234",
        )
        assert result is None

    def test_localhost_http_not_blocked(self):
        # Should not be blocked by the HTTP check (will fail with connection error, not None-from-check)
        from synapptic.providers import call_openai_compatible
        import unittest.mock as mock
        with mock.patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            try:
                call_openai_compatible(
                    prompt="test",
                    model="gpt-4",
                    api_url="http://127.0.0.1:11434/v1",
                    api_key="",
                )
            except OSError:
                pass  # reached the network call — not blocked by HTTP check

    def test_subdomain_lookalike_blocked(self):
        """http://localhost.attacker.com must be blocked — hostname is not 'localhost'."""
        from synapptic.providers import call_openai_compatible
        result = call_openai_compatible(
            prompt="test",
            model="gpt-4",
            api_url="http://localhost.attacker.com/v1",
            api_key="sk-test1234",
        )
        assert result is None

    def test_file_scheme_blocked(self):
        """file:// URLs must be rejected before any network call."""
        from synapptic.providers import call_openai_compatible
        result = call_openai_compatible(
            prompt="test",
            model="gpt-4",
            api_url="file:///etc/passwd",
            api_key="",
        )
        assert result is None


class TestCallLlmDispatch:

    def test_dispatches_to_anthropic(self):
        from synapptic.providers import call_llm
        config = {"provider": "anthropic", "api_key": "sk-test", "model": "claude-3-haiku-20240307"}
        with patch("synapptic.providers.call_anthropic", return_value="response") as mock_fn:
            result = call_llm("hello", config=config)
        assert result == "response"
        mock_fn.assert_called_once()

    def test_dispatches_to_ollama(self):
        from synapptic.providers import call_llm
        config = {"provider": "ollama", "api_url": "http://localhost:11434/v1", "model": "llama3"}
        with patch("synapptic.providers.call_ollama", return_value="ollama reply") as mock_fn:
            result = call_llm("hello", config=config)
        assert result == "ollama reply"
        mock_fn.assert_called_once()

    def test_unknown_provider_returns_none(self):
        from synapptic.providers import call_llm
        result = call_llm("hello", config={"provider": "nonexistent_provider_xyz"})
        assert result is None

    def test_empty_config_returns_none(self):
        from synapptic.providers import call_llm
        result = call_llm("hello", config={})
        assert result is None


class TestRetryLogic:

    def setup_method(self):
        from synapptic.providers import reset_rate_limits
        reset_rate_limits()

    def test_503_retries_and_returns_on_success(self):
        """A 503 on first attempt should retry and return the second response."""
        from synapptic.providers import call_llm, _last_http_status
        import synapptic.providers as prov

        call_count = 0

        def fake_dispatch(prompt, provider, model, config, temperature, session_id):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                prov._last_http_status = 503
                prov._last_http_body = ""
                return None
            prov._last_http_status = 200
            return "success after retry"

        config = {"provider": "anthropic", "model": "claude-3-haiku-20240307", "api_key": "sk-test"}
        with patch("synapptic.providers._dispatch_call", side_effect=fake_dispatch):
            with patch("synapptic.providers._countdown"):  # skip sleep
                result = call_llm("hello", config=config)

        assert result == "success after retry"
        assert call_count == 2

    def test_daily_limit_stops_retrying(self):
        """A 429 with 'daily' in the body should not retry."""
        from synapptic.providers import call_llm
        import synapptic.providers as prov

        call_count = 0

        def fake_dispatch(prompt, provider, model, config, temperature, session_id):
            nonlocal call_count
            call_count += 1
            prov._last_http_status = 429
            prov._last_http_body = "You have exceeded your daily limit"
            return None

        config = {"provider": "anthropic", "model": "claude-3-haiku-20240307", "api_key": "sk-test"}
        with patch("synapptic.providers._dispatch_call", side_effect=fake_dispatch):
            with patch("synapptic.providers._countdown"):
                result = call_llm("hello", config=config)

        assert result is None
        assert call_count == 1  # stopped immediately on daily limit
