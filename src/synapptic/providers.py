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
from pathlib import Path

import yaml


PROVIDERS = {
    "claude-cli": {
        "name": "Claude CLI",
        "description": "Uses `claude -p` command (requires Claude Code installed and authenticated)",
        "requires_key": False,
        "requires_url": False,
    },
    "anthropic": {
        "name": "Anthropic API",
        "description": "Direct API calls (requires ANTHROPIC_API_KEY)",
        "requires_key": True,
        "requires_url": False,
        "default_model": "claude-sonnet-4-20250514",
    },
    "openai": {
        "name": "OpenAI API",
        "description": "OpenAI-compatible API (requires OPENAI_API_KEY)",
        "requires_key": True,
        "requires_url": False,
        "default_model": "gpt-4o",
    },
    "ollama": {
        "name": "Ollama (local)",
        "description": "Local Ollama server at localhost:11434 (free, no API key)",
        "requires_key": False,
        "requires_url": False,
        "default_model": "llama3.1",
        "default_url": "http://localhost:11434/v1",
    },
    "lmstudio": {
        "name": "LM Studio (local)",
        "description": "Local LM Studio server at localhost:1234 (free, no API key)",
        "requires_key": False,
        "requires_url": False,
        "default_model": "local-model",
        "default_url": "http://localhost:1234/v1",
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
    return {
        "provider": "claude-cli",
        "model": "sonnet",
    }


def save_config(config: dict):
    """Save synaptic configuration."""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
    with open(tmp, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    tmp.rename(CONFIG_PATH)


def call_llm(prompt: str, config: dict | None = None) -> str | None:
    """Send a prompt to the configured LLM provider and return the text response.

    Returns None on failure.
    """
    if config is None:
        config = load_config()

    provider = config.get("provider", "claude-cli")
    model = config.get("model") or PROVIDERS.get(provider, {}).get("default_model", "sonnet")

    if provider == "claude-cli":
        return call_claude_cli(prompt, model)
    elif provider == "anthropic":
        return call_anthropic(prompt, model, config.get("api_key", ""))
    elif provider in ("openai", "ollama", "lmstudio", "custom"):
        url = config.get("api_url", PROVIDERS.get(provider, {}).get("default_url", ""))
        key = config.get("api_key", "")
        return call_openai_compatible(prompt, model, url, key)
    else:
        print(f"Unknown provider: {provider}", file=sys.stderr)
        return None


def call_claude_cli(prompt: str, model: str = "sonnet") -> str | None:
    """Call claude -p with tools disabled."""
    try:
        result = subprocess.run(
            ["claude", "-p", "--model", model, "--output-format", "text", "--tools", ""],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.TimeoutExpired:
        print("LLM call timed out (claude -p)", file=sys.stderr)
        return None
    except KeyboardInterrupt:
        print("\nLLM call interrupted", file=sys.stderr)
        return None
    except FileNotFoundError:
        print("claude CLI not found. Install Claude Code or use a different provider.", file=sys.stderr)
        return None

    if result.returncode != 0:
        err = result.stderr.strip() or result.stdout.strip()
        print(f"claude -p error (exit {result.returncode}): {err[:300]}", file=sys.stderr)
        return None

    return result.stdout.strip()


def call_anthropic(prompt: str, model: str, api_key: str) -> str | None:
    """Call Anthropic API directly using urllib (no SDK dependency)."""
    import urllib.request
    import urllib.error

    if not api_key:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        print("No ANTHROPIC_API_KEY configured. Run `synaptic init` or set the environment variable.", file=sys.stderr)
        return None

    body = json.dumps({
        "model": model,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": prompt}],
    }).encode()

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


def call_openai_compatible(prompt: str, model: str, api_url: str, api_key: str = "") -> str | None:
    """Call any OpenAI-compatible API (OpenAI, Ollama, LMStudio, custom)."""
    import urllib.request
    import urllib.error
    import os

    if not api_url:
        print("No API URL configured. Run `synaptic init`.", file=sys.stderr)
        return None

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")

    url = api_url.rstrip("/") + "/chat/completions"

    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
    }).encode()

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
