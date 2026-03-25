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
import re
import subprocess
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import yaml


_REDACT_RE = re.compile(
    r'((?:sk-|key-|token-|gsk_|AIza|Bearer\s)[a-zA-Z0-9_-]{4})[a-zA-Z0-9_-]+'
)


def redact_secrets(text: str) -> str:
    """Redact API keys and auth tokens from text (error messages, HTTP bodies)."""
    return _REDACT_RE.sub(r'\1...', text)


# Global call tracker - accumulates across all LLM calls in a session
_call_totals = {
    "calls": 0,
    "prompt_tokens": 0,
    "response_tokens": 0,
    "cache_create": 0,
    "cache_read": 0,
    "total_tokens": 0,
    "cost_usd": 0.0,
    "elapsed_sec": 0.0,
}


def track_call(metrics: dict):
    """Accumulate metrics from a single LLM call."""
    for key in _call_totals:
        if key == "calls":
            _call_totals["calls"] += 1
        elif key in metrics:
            _call_totals[key] += metrics[key]


def get_call_totals() -> dict:
    """Return accumulated call totals."""
    return dict(_call_totals)


def reset_call_totals():
    """Reset the call tracker."""
    for key in _call_totals:
        _call_totals[key] = 0 if key != "cost_usd" else 0.0


# --- Rate Limiting (flat, no nesting) ---

# Default limits per provider (free tier where applicable)
# Per-provider, per-model rate limits. "default" is the fallback for unlisted models.
PROVIDER_RATE_LIMITS = {
    "claude-cli": {
        "default": {"tpm": 100_000, "rpm": 30},
    },
    "anthropic": {
        "default": {"tpm": 400_000, "rpm": 50},
    },
    "openai": {
        "default": {"tpm": 200_000, "rpm": 60},
    },
    "ollama": {},
    "lmstudio": {},
    "gemini": {
        "default": {"tpm": 250_000, "rpm": 15},
        "gemini-2.5-flash": {"tpm": 250_000, "rpm": 5},
        "gemini-2.5-flash-lite": {"tpm": 250_000, "rpm": 10},
        "gemini-2.0-flash": {"tpm": 250_000, "rpm": 15},
        "gemini-2.0-flash-lite": {"tpm": 250_000, "rpm": 30},
        "gemini-3.1-flash-lite-preview": {"tpm": 250_000, "rpm": 15},
    },
    "groq": {
        "default": {"tpm": 6_000, "rpm": 30},
        "llama-3.3-70b-versatile": {"tpm": 12_000, "rpm": 30},
        "llama-3.1-8b-instant": {"tpm": 6_000, "rpm": 30},
        "gemma2-9b-it": {"tpm": 15_000, "rpm": 30},
        "meta-llama/llama-4-scout-17b-16e-instruct": {"tpm": 30_000, "rpm": 30},
        "mixtral-8x7b-32768": {"tpm": 15_000, "rpm": 30},
    },
    "custom": {},
}

# Sliding window state: deque of (timestamp,) for RPM, deque of (timestamp, tokens) for TPM
_rpm_log: deque = deque()
_tpm_log: deque = deque()

MAX_RETRIES = 3
RETRY_BASE_DELAY = 15.0  # seconds — TPM windows are 60s, need real wait time


def _prune_rate_window():
    """Remove entries older than 60 seconds from both logs."""
    cutoff = time.time() - 60.0
    while _rpm_log and _rpm_log[0] < cutoff:
        _rpm_log.popleft()
    while _tpm_log and _tpm_log[0][0] < cutoff:
        _tpm_log.popleft()


def _resolve_limits(config: dict | None) -> tuple[int, int]:
    """Return (tpm, rpm) for the current provider + model. 0 = unlimited."""
    if not config:
        return 0, 0
    # User overrides in config.limits
    user_limits = config.get("limits", {})
    if user_limits:
        return user_limits.get("tpm", 0), user_limits.get("rpm", 0)
    # Auto-detect known providers from URL
    provider = config.get("provider", "")
    if provider == "custom":
        url = config.get("api_url", "")
        for name in ("groq", "gemini", "anthropic", "openai"):
            if name in url:
                provider = name
                break
    provider_limits = PROVIDER_RATE_LIMITS.get(provider, {})
    if not provider_limits:
        return 0, 0
    # Try exact model match first, then fall back to "default"
    model = config.get("model", "")
    model_limits = provider_limits.get(model) or provider_limits.get("default", {})
    return model_limits.get("tpm", 0), model_limits.get("rpm", 0)


def estimate_tokens(text: str) -> int:
    """Conservative token estimate from character count (3 chars ≈ 1 token).

    Industry standard is ~4 chars/token for English BPE tokenizers. This uses 3
    intentionally — overshooting by ~33% so the rate limiter throttles proactively
    rather than relying on 429 retries. The server counts both prompt AND response
    tokens against TPM, and we can't predict response length, so the overshoot
    absorbs that gap.

    Note: config.py:CHARS_PER_TOKEN=4 is used for user-facing budget calculation
    in filter.py (transcript truncation). The two intentionally differ.
    """
    return max(1, len(text) // 3)


def rate_limit_wait(estimated_tokens: int, config: dict | None = None):
    """Sleep if the next call would exceed RPM or TPM limits."""
    tpm_limit, rpm_limit = _resolve_limits(config)
    if not tpm_limit and not rpm_limit:
        return
    while True:
        now = time.time()
        _prune_rate_window()
        wait = 0.0
        if rpm_limit and len(_rpm_log) >= rpm_limit:
            wait = max(wait, (_rpm_log[0] + 60.0) - now + 0.1)
        if tpm_limit and estimated_tokens > 0:
            current_tpm = sum(t[1] for t in _tpm_log)
            if current_tpm + estimated_tokens > tpm_limit and _tpm_log:
                wait = max(wait, (_tpm_log[0][0] + 60.0) - now + 0.1)
        if wait < 1.0:
            if wait > 0:
                time.sleep(wait)
            break
        current_tpm = sum(t[1] for t in _tpm_log)
        _countdown(
            wait,
            f"  [rate-limit] {{remaining}}s "
            f"(rpm: {len(_rpm_log)}/{rpm_limit or '∞'}, "
            f"tpm: {current_tpm}/{tpm_limit or '∞'})",
        )


def rate_limit_record(token_count: int):
    """Record a completed call for rate tracking."""
    now = time.time()
    _rpm_log.append(now)
    if token_count > 0:
        _tpm_log.append((now, token_count))


def _parse_usage_from_429(body: str) -> int:
    """Extract 'Used NNNNN' from a 429 error body. Returns 0 if not found."""
    match = re.search(r'Used\s+(\d+)', body)
    return int(match.group(1)) if match else 0


def _countdown(seconds: float, template: str):
    """Print a countdown timer. Template must contain {remaining} placeholder."""
    remaining = int(seconds)
    while remaining > 0:
        print(f"\r{template.format(remaining=remaining)}  ", end="", file=sys.stderr, flush=True)
        time.sleep(1)
        remaining -= 1
    # Clear countdown and end with newline so next output starts fresh
    print(f"\r{' ' * 80}\r", end="", file=sys.stderr, flush=True)
    print(file=sys.stderr)  # newline


def get_tpm_headroom(config: dict | None = None) -> float:
    """Return fraction of TPM still available (0.0 = full, 1.0 = empty). -1 if no limit."""
    tpm_limit, _ = _resolve_limits(config)
    if not tpm_limit:
        return -1.0
    _prune_rate_window()
    current = sum(t[1] for t in _tpm_log)
    return 1.0 - (current / tpm_limit)


def reset_rate_limits():
    """Clear rate limit tracking state."""
    _rpm_log.clear()
    _tpm_log.clear()


def is_daily_limit_hit() -> bool:
    """Check if the last 429 was a daily limit (unrecoverable within session)."""
    if _last_http_status != 429:
        return False
    body_lower = _last_http_body.lower()
    return "per day" in body_lower or "per_day" in body_lower or "daily" in body_lower


# Last HTTP status and error body from call functions (used for 429 retry detection)
_last_http_status = 0
_last_http_body = ""


# Claude CLI session reuse
_last_claude_session_id = None


def get_claude_session_id() -> str | None:
    """Get the session_id from the last claude -p call for reuse."""
    return _last_claude_session_id


def reset_claude_session():
    """Clear the stored session_id (start fresh)."""
    global _last_claude_session_id
    _last_claude_session_id = None


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
            ("qwen3-coder-next:q4_K_M", "Qwen 3 Coder Next (coding-optimized, recommended)"),
            ("qwen2.5:72b-instruct-q4_K_M", "Qwen 2.5 72B (Q4)"),
            ("llama3.3:70b-instruct-q4_K_M", "Llama 3.3 70B (Q4)"),
            ("gemma3:27b", "Gemma 3 27B"),
            ("deepseek-r1:70b", "DeepSeek R1 70B"),
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
            ("gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite"),
            ("gemini-2.5-flash", "Gemini 2.5 Flash"),
            ("gemini-2.0-flash", "Gemini 2.0 Flash"),
            ("gemini-2.0-flash-lite", "Gemini 2.0 Flash Lite"),
        ],
    },
    "groq": {
        "name": "Groq",
        "description": "Groq API (requires GROQ_API_KEY)",
        "requires_key": True,
        "requires_url": False,
        "default_model": "meta-llama/llama-4-scout-17b-16e-instruct",
        "default_url": "https://api.groq.com/openai/v1",
        "models": [
            ("meta-llama/llama-4-scout-17b-16e-instruct", "Llama 4 Scout 17B (recommended)"),
            ("llama-3.3-70b-versatile", "Llama 3.3 70B Versatile"),
            ("llama-3.1-8b-instant", "Llama 3.1 8B Instant (fast)"),
            ("gemma2-9b-it", "Gemma 2 9B"),
            ("mixtral-8x7b-32768", "Mixtral 8x7B"),
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
    """Save synaptic configuration. Restricts permissions to owner-only (0600) since it may contain API keys."""
    import os
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".yaml.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)
    tmp.rename(CONFIG_PATH)


def _dispatch_call(prompt, provider, model, config, temperature, session_id):
    """Route a single call to the right provider function. No retry logic."""
    if provider == "claude-cli":
        return call_claude_cli(prompt, model, temperature=temperature, session_id=session_id)
    if provider == "anthropic":
        return call_anthropic(prompt, model, config.get("api_key", ""), temperature=temperature)
    if provider == "ollama":
        url = config.get("api_url", PROVIDERS.get(provider, {}).get("default_url", ""))
        return call_ollama(prompt, model, url, config, temperature=temperature)
    if provider in ("openai", "gemini", "groq", "lmstudio", "custom"):
        url = config.get("api_url", PROVIDERS.get(provider, {}).get("default_url", ""))
        key = config.get("api_key", "")
        return call_openai_compatible(prompt, model, url, key, temperature=temperature)
    print(f"Unknown provider: {provider}", file=sys.stderr)
    return None


def call_llm(prompt: str, config: dict | None = None, temperature: float | None = None, session_id: str | None = None) -> str | None:
    """Send a prompt to the configured LLM provider and return the text response.

    Applies rate limiting (TPM/RPM) and retries with exponential backoff on 429.
    Returns None on failure.
    """
    if config is None:
        config = load_config()

    provider = config.get("provider")
    if not provider:
        print("No provider configured. Run `synapptic init` to select one.", file=sys.stderr)
        return None
    model = config.get("model") or PROVIDERS.get(provider, {}).get("default_model", "sonnet")

    global _last_http_status, _last_http_body
    _last_http_status = 0
    _last_http_body = ""
    est_tokens = estimate_tokens(prompt)
    rate_limit_wait(est_tokens, config)

    for attempt in range(MAX_RETRIES + 1):
        result = _dispatch_call(prompt, provider, model, config, temperature, session_id)
        if result is not None:
            # estimate_tokens() uses //3 (conservative) — no extra multiplier needed
            rate_limit_record(est_tokens)
            return result
        if _last_http_status not in (429, 503) or attempt >= MAX_RETRIES:
            return None
        # 503: service overloaded — short wait then retry
        if _last_http_status == 503:
            delay = 10 * (attempt + 1)
            _countdown(delay, f"  [retry] 503 unavailable, {{remaining}}s (attempt {attempt + 1}/{MAX_RETRIES})")
            continue
        # Daily limit (TPD/RPD) — no point retrying
        body_lower = _last_http_body.lower()
        if "per day" in body_lower or "per_day" in body_lower or "daily" in body_lower:
            print("  [rate-limit] daily limit reached — cannot retry, aborting", file=sys.stderr)
            return None
        # Parse actual usage from 429 body to calibrate our tracker
        server_used = _parse_usage_from_429(_last_http_body)
        if server_used > 0:
            reset_rate_limits()
            rate_limit_record(server_used)

        # TPM 429: wait for the full 60s window to clear
        _countdown(60, f"  [retry] 429 window reset {{remaining}}s (attempt {attempt + 1}/{MAX_RETRIES})")
        reset_rate_limits()

    return None


def call_claude_cli(prompt: str, model: str = "sonnet", temperature: float | None = None, session_id: str | None = None) -> str | None:
    """Call claude -p with JSON output for token/cost tracking.

    session_id: reuse an existing session (avoids reloading full context).
    Returns the session_id in _last_claude_session_id for callers to reuse.
    """
    try:
        cmd = ["claude", "-p", "--model", model, "--output-format", "json", "--tools", ""]
        if session_id:
            cmd.extend(["--resume", session_id])
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
        raise
    except FileNotFoundError:
        print("claude CLI not found. Install Claude Code or use a different provider.", file=sys.stderr)
        return None

    if result.returncode != 0:
        err = redact_secrets((result.stderr.strip() or result.stdout.strip())[:200])
        print(f"claude -p error (exit {result.returncode}): {err}", file=sys.stderr)
        return None

    # Parse JSON response for text + metrics
    raw = result.stdout.strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return raw

    # Store session_id for reuse
    global _last_claude_session_id
    _last_claude_session_id = data.get("session_id")

    # Extract metrics
    usage = data.get("usage", {})
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    cache_create = usage.get("cache_creation_input_tokens", 0)
    cache_read = usage.get("cache_read_input_tokens", 0)
    cost = data.get("total_cost_usd", 0)
    duration = data.get("duration_ms", 0) / 1000

    metrics = {
        "prompt_tokens": input_tokens,
        "response_tokens": output_tokens,
        "cache_create": cache_create,
        "cache_read": cache_read,
        "total_tokens": input_tokens + output_tokens,
        "cost_usd": cost,
        "elapsed_sec": duration,
    }
    track_call(metrics)

    if output_tokens > 0:
        tps = output_tokens / duration if duration > 0 else 0
        print(
            f"  [tokens] {format_tokens(output_tokens)} gen | {format_tokens(input_tokens)} prompt | "
            f"cache: {format_tokens(cache_read)}r/{format_tokens(cache_create)}w | "
            f"${cost:.4f} | {duration:.1f}s | {tps:.1f} tok/s",
            file=sys.stderr,
        )

    # Extract response text
    response_text = data.get("result", "")
    return response_text.strip() if response_text else None


def call_anthropic(prompt: str, model: str, api_key: str, temperature: float | None = None) -> str | None:
    """Call Anthropic API directly using urllib (no SDK dependency)."""
    import urllib.request
    import urllib.error

    if not api_key:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            print("Warning: using ANTHROPIC_API_KEY from environment (not in config)", file=sys.stderr)
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

    global _last_http_status, _last_http_body
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            _last_http_status = resp.status
            _last_http_body = ""
            data = json.loads(resp.read())
            for block in data.get("content", []):
                if block.get("type") == "text":
                    return block["text"].strip()
            return None
    except urllib.error.HTTPError as e:
        _last_http_status = e.code
        body = redact_secrets(e.read().decode()[:500])
        _last_http_body = body
        print(f"Anthropic API error {e.code}: {body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        _last_http_status = 0
        _last_http_body = ""
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

    if not api_url.startswith(("http://", "https://")):
        print(f"Error: unsupported URL scheme in api_url (only http/https allowed): {api_url[:50]}", file=sys.stderr)
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
            track_call(metrics)
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
        global _last_http_status, _last_http_body
        _last_http_status = e.code
        body = redact_secrets(e.read().decode()[:500])
        _last_http_body = body
        print(f"Ollama API error {e.code}: {body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        _last_http_status = 0
        _last_http_body = ""
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
        print("No API URL configured. Run `synapptic init`.", file=sys.stderr)
        return None

    if not api_url.startswith(("http://", "https://")):
        print(f"Error: unsupported URL scheme in api_url (only http/https allowed): {api_url[:50]}", file=sys.stderr)
        return None

    if api_url.startswith("http://"):
        from urllib.parse import urlparse as _urlparse
        _hostname = (_urlparse(api_url).hostname or "").lower()
        is_loopback = _hostname in {"localhost", "127.0.0.1", "::1"}
        if not is_loopback:
            print(f"Error: refusing to send API key over HTTP to non-localhost URL: {api_url[:50]}", file=sys.stderr)
            return None

    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("GEMINI_API_KEY", "")
        if api_key:
            env_name = "OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else "GEMINI_API_KEY"
            print(f"Warning: using {env_name} from environment (not in config)", file=sys.stderr)

    url = api_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 4096,
    }
    if temperature is not None:
        payload["temperature"] = temperature
    body = json.dumps(payload).encode()

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "synapptic/0.1",
    }
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
        global _last_http_status, _last_http_body
        _last_http_status = e.code
        body = redact_secrets(e.read().decode()[:500])
        _last_http_body = body
        print(f"API error {e.code}: {body[:200]}", file=sys.stderr)
        return None
    except Exception as e:
        _last_http_status = 0
        _last_http_body = ""
        print(f"API error: {e}", file=sys.stderr)
        return None
