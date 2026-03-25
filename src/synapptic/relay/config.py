"""Proxy configuration — ports, upstream URLs, feature flags."""

from pathlib import Path

# Server
PROXY_HOST = "127.0.0.1"
PROXY_PORT = 5100

# Upstream provider base URLs
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
OPENAI_BASE_URL = "https://api.openai.com"

# Allowed upstream domains (SSRF prevention)
ALLOWED_UPSTREAM_HOSTS = frozenset({
    "api.anthropic.com",
    "api.openai.com",
})

# State
SYNAPPTIC_DIR = Path.home() / ".synapptic"
DB_PATH = SYNAPPTIC_DIR / "proxy.db"

# Session
SESSION_TIMEOUT_MINUTES = 30

# Feature flags (Phase 1: all OFF)
ENABLE_CURATION = False
ENABLE_PROFILE_INJECTION = False
ENABLE_TOOL_PRUNING = False

# Pricing per million tokens (USD) — for cost estimation
MODEL_PRICING = {
    # Anthropic
    "claude-opus-4-20250514": {"input": 15.0, "output": 75.0},
    "claude-opus-4-0": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-20250514": {"input": 3.0, "output": 15.0},
    "claude-sonnet-4-0": {"input": 3.0, "output": 15.0},
    "claude-haiku-4-5-20251001": {"input": 1.0, "output": 5.0},
    # Extended thinking variants
    "claude-opus-4-20250514-extended": {"input": 15.0, "output": 75.0},
    "claude-sonnet-4-20250514-extended": {"input": 3.0, "output": 15.0},
    # OpenAI
    "gpt-4o": {"input": 2.50, "output": 10.0},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4.1": {"input": 2.0, "output": 8.0},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-4.1-nano": {"input": 0.10, "output": 0.40},
    "o3": {"input": 2.0, "output": 8.0},
    "o3-mini": {"input": 1.10, "output": 4.40},
    "o4-mini": {"input": 1.10, "output": 4.40},
}

# Fallback pricing for unknown models
DEFAULT_PRICING = {"input": 3.0, "output": 15.0}

# Chars per token estimate (fallback when API doesn't report usage)
CHARS_PER_TOKEN = 4
