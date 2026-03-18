"""LLM provider abstraction for synapptic.

Supports multiple backends for the extraction and synthesis LLM calls:
- claude-cli: Uses `claude -p` (default, works with Pro/Max plan)
- anthropic: Direct Anthropic API (needs ANTHROPIC_API_KEY)
- openai: OpenAI-compatible API (GPT-4o, needs OPENAI_API_KEY)
- ollama: Local Ollama server (free, no API key)
- lmstudio: Local LMStudio server (free, no API key)
- custom: Any OpenAI-compatible endpoint (custom URL + optional key)
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import yaml


PROVIDERS = {
    "claude-cli": {
        "name": "Claude CLI",
        "description": "Uses `claude -p` command (requires Claude Code installed and authenticated)",
        "requires_key": False,
        "requires_url": False,
        "default_model": "sonnet",
        "models": [
            ("sonnet", "Claude Sonnet (balanced, recommended)"),
            ("opus", "Claude Opus (most capable)"),
            ("haiku", "Claude Haiku (fast, small)"),
        ],
    },
    "anthropic": {
        "name": "Anthropic API",
        "description": "Direct API calls (requires ANTHROPIC_API_KEY)",
        "requires_key": True,
        "requires_url": False,
        "default_model": "claude-sonnet-4-20250514",
        "models": [
            ("claude-sonnet-4-20250514", "Claude Sonnet (balanced, recommended)"),
            ("claude-opus-4-20250805", "Claude Opus (most capable)"),
            ("claude-haiku-4-5-20251001", "Claude Haiku (fast, small)"),
        ],
    },
    "openai": {
        "name": "OpenAI API",
        "description": "OpenAI-compatible API (requires OPENAI_API_KEY)",
        "requires_key": True,
        "requires_url": False,
        "default_model": "gpt-4o",
        "models": [
            ("gpt-4o", "GPT-4o (latest, recommended)"),
            ("gpt-4-turbo", "GPT-4 Turbo"),
            ("gpt-4", "GPT-4"),
            ("gpt-3.5-turbo", "GPT-3.5 Turbo (fast, cheap)"),
        ],
    },
    "ollama": {
        "name": "Ollama (local)",
        "description": "Local Ollama server at localhost:11434 (free, no API key)",
        "requires_key": False,
        "requires_url": False,
        "default_model": "qwen3-coder-next:q4_K_M",
        "default_url": "http://localhost:11434/v1",
        "models": [
            ("qwen3-coder-next:q4_K_M", "Qwen Coder Next (coding-optimized, recommended)"),
            ("llama3.2:latest", "Llama 3.2 (general purpose)"),
            ("mistral:latest", "Mistral (fast, capable)"),
            ("neural-chat:latest", "Neural Chat (conversation-optimized)"),
        ],
    },
    "lmstudio": {
        "name": "LM Studio (local)",
        "description": "Local LM Studio server at localhost:1234 (free, no API key)",
        "requires_key": False,
        "requires_url": False,
        "default_model": "local-model",
        "default_url": "http://localhost:1234/v1",
        "models": [
            ("local-model", "Currently loaded model"),
        ],
    },
    "gemini": {
        "name": "Google Gemini",
        "description": "Gemini API (requires GEMINI_API_KEY)",
        "requires_key": True,
        "requires_url": False,
        "default_model": "gemini-3.1-flash-lite-preview",
        "default_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "models": [
            ("gemini-3.1-flash-lite-preview", "Gemini 3.1 Flash Lite (fast, recommended)"),
            ("gemini-2.0-flash", "Gemini 2.0 Flash"),
        ],
    },
    "custom": {
        "name": "Custom endpoint",
        "description": "Any OpenAI-compatible API endpoint",
        "requires_key": False,
        "requires_url": True,
    },
}

CONFIG_PATH = Path.home() / ".synapptic" / "config.yaml"


def load_config() -> dict:
    """Load synaptic configuration. Returns defaults if no config file."""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH) as f:
                data = yaml.safe_load(f)
                if isinstance(data, dict):
                    return data
        except (yaml.YAMLError, OSError):
            pass
    return {}


def save_config(config: dict):
    """Save synaptic configuration."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
    with open(tmp, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    tmp.rename(CONFIG_PATH)


def call_llm(prompt: str, config: dict | None = None, temperature: float | None = None) -> str | None:
    """Send a prompt to the configured LLM provider and return the text response.

    temperature: explicit temperature override. None means use provider default.
    Returns None on failure.
    """
    if config is None:
        config = load_config()

    provider = config.get("provider")
    if not provider:
        print("No provider configured. Run `synapptic init` to select one.", file=sys.stderr)
        return None
    model = config.get("model") or PROVIDERS.get(provider, {}).get("default_model", "sonnet")

    if provider == "claude-cli":
        return call_claude_cli(prompt, model, temperature=temperature)
    elif provider == "anthropic":
        return call_anthropic(prompt, model, config.get("api_key", ""), temperature=temperature)
    elif provider == "ollama":
        url = config.get("api_url", PROVIDERS.get(provider, {}).get("default_url", ""))
        return call_ollama(prompt, model, url, config, temperature=temperature)
    elif provider in ("openai", "gemini", "lmstudio", "custom"):
        url = config.get("api_url", PROVIDERS.get(provider, {}).get("default_url", ""))
        key = config.get("api_key", "")
        return call_openai_compatible(prompt, model, url, key, temperature=temperature)
    else:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        return None


def call_claude_cli(prompt: str, model: str = "sonnet", temperature: float | None = None) -> str | None:
    """Call claude -p with tools disabled.

    Note: claude CLI does not support --temperature, so the parameter is
    accepted for interface consistency but silently ignored.
    """
    try:
        cmd = ["claude", "-p", "--model", model, "--output-format", "text", "--tools", ""]
        result = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("LLM call timed out (claude -p)", file=sys.stderr)
        return None
    except KeyboardInterrupt:
        raise  # let it propagate to kill the whole command
    except FileNotFoundError:
        print("claude CLI not found. Install Claude Code or use a different provider.", file=sys.stderr)
        return None

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        print(f"claude -p error (exit {result.returncode}): {err[:500]}", file=sys.stderr)
        return None

    return result.stdout.strip()


def call_anthropic(prompt: str, model: str, api_key: str, temperature: float | None = None) -> str | None:
    """Call Anthropic API directly using urllib (no SDK dependency)."""
    import urllib.request
    import urllib.error

    if not api_key:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("No ANTHROPIC_API_KEY configured. Run `synaptic init` or set the environment variable.", file=sys.stderr)
        return None

    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode()

    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block["text"].strip()
            return None
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"Anthropic API error {e.code}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Anthropic API error: {e}", file=sys.stderr)
        return None


def call_ollama(prompt: str, model: str, api_url: str, config: dict, temperature: float | None = None) -> str | None:
    """Call Ollama's native API with optimization parameters (num_ctx, num_gpu, etc).

    Returns response text. Token metrics stored in _last_ollama_metrics.
    """
    import urllib.request
    import urllib.error
    import time

    if not api_url:
        print("No API URL configured for Ollama. Run `synapptic config provider`.", file=sys.stderr)
        return None

    # Extract Ollama-specific parameters from config
    num_ctx = config.get("ollama_num_ctx", 65536)  # default 64k context
    num_gpu = config.get("ollama_num_gpu", 99)     # default max GPU layers
    # Explicit temperature param overrides config value
    if temperature is None:
        temperature = config.get("ollama_temperature", 0.2)
    top_p = config.get("ollama_top_p", 0.9)

    # Ollama native API endpoint: strip /v1 from OpenAI-compat URL and use /api/generate
    base_url = api_url.rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]

    url = base_url + "/api/generate"

    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "num_ctx": num_ctx,
            "num_gpu": num_gpu,
            "temperature": temperature,
            "top_p": top_p,
        }
    }).encode()

    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"}
    )

    try:
        start_time = time.time()
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            elapsed = time.time() - start_time

            response_text = data.get("response", "").strip()

            # Extract token metrics from Ollama response
            prompt_tokens = data.get("prompt_eval_count", 0)
            response_tokens = data.get("eval_count", 0)
            total_tokens = prompt_tokens + response_tokens
            tokens_per_sec = response_tokens / elapsed if elapsed > 0 else 0

            # Store metrics globally and persistently for monitoring
            metrics = {
                "prompt_tokens": prompt_tokens,
                "response_tokens": response_tokens,
                "total_tokens": total_tokens,
                "elapsed_sec": elapsed,
                "tokens_per_sec": tokens_per_sec,
            }
            global _last_ollama_metrics
            _last_ollama_metrics = metrics
            log_token_metrics(metrics)

            # Print live metrics during operation
            if response_tokens > 0:
                print(
                    f"  [tokens] {format_tokens(response_tokens)} gen | {format_tokens(prompt_tokens)} prompt | "
                    f"{elapsed:.1f}s | {tokens_per_sec:.1f} tok/s",
                    file=sys.stderr
                )

            if response_text:
                return response_text
            return None
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"Ollama API error {e.code}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"Ollama API error: {e}", file=sys.stderr)
        return None


# Global variable to store last token metrics
_last_ollama_metrics = {}


def get_last_ollama_metrics() -> dict:
    """Get token metrics from the last Ollama call.

    Returns dict with keys: prompt_tokens, response_tokens, total_tokens, elapsed_sec, tokens_per_sec
    """
    return _last_ollama_metrics.copy()


def format_tokens(count: int) -> str:
    """Format token count as human-readable with k suffix.

    Examples: 1234 → 1.2k, 12345 → 12.3k, 1000000 → 1000.0k
    """
    if count >= 1000:
        return f"{count / 1000:.1f}k"
    return str(count)


def log_token_metrics(metrics: dict):
    """Log token metrics to a file for monitoring.

    Appends to ~/.synapptic/token_metrics.jsonl
    """
    from pathlib import Path
    from synapptic.config import SYNAPPTIC_DIR

    metrics_file = SYNAPPTIC_DIR / "token_metrics.jsonl"
    metrics_with_timestamp = {
        "timestamp": datetime.now().isoformat(),
        **metrics
    }

    try:
        with open(metrics_file, "a") as f:
            f.write(json.dumps(metrics_with_timestamp) + "\n")
    except Exception:
        pass  # Silently fail if can't write metrics


def get_token_metrics_summary(limit: int = 10) -> dict:
    """Get summary statistics from recent token metrics.

    Returns aggregate stats from last N calls.
    """
    from pathlib import Path
    from synapptic.config import SYNAPPTIC_DIR

    metrics_file = SYNAPPTIC_DIR / "token_metrics.jsonl"
    if not metrics_file.exists():
        return {}

    recent = []
    try:
        with open(metrics_file) as f:
            for line in f:
                if line.strip():
                    recent.append(json.loads(line))
        recent = recent[-limit:]
    except Exception:
        return {}

    if not recent:
        return {}

    # Calculate aggregates
    total_prompt = sum(m.get("prompt_tokens", 0) for m in recent)
    total_response = sum(m.get("response_tokens", 0) for m in recent)
    total_time = sum(m.get("elapsed_sec", 0) for m in recent)
    avg_tokens_per_sec = sum(m.get("tokens_per_sec", 0) for m in recent) / len(recent)

    return {
        "calls": len(recent),
        "total_prompt_tokens": total_prompt,
        "total_response_tokens": total_response,
        "total_tokens": total_prompt + total_response,
        "total_time_sec": total_time,
        "avg_tokens_per_sec": avg_tokens_per_sec,
        "avg_time_per_call": total_time / len(recent) if recent else 0,
    }


def call_openai_compatible(prompt: str, model: str, api_url: str, api_key: str = "", temperature: float | None = None) -> str | None:
    """Call any OpenAI-compatible API (OpenAI, Ollama, LMStudio, custom)."""
    import urllib.request
    import urllib.error
    import os

    if not api_url:
        print("No API URL configured. Run `synaptic init`.", file=sys.stderr)
        return None

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")

    url = api_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode()

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(url, data=body, headers=headers)

    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = json.loads(resp.read())
            choices = data.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "").strip()
            return None
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:200]
        print(f"API error {e.code}: {body}", file=sys.stderr)
        return None
    except Exception as e:
        print(f"API error: {e}", file=sys.stderr)
        return None
