"""Async HTTP forwarding via httpx — stream and non-stream."""

import httpx
from fastapi import Request

from synapptic.relay.config import ANTHROPIC_BASE_URL, OPENAI_BASE_URL

# Headers that must not be forwarded (hop-by-hop per RFC 7230 + privacy)
STRIP_HEADERS = frozenset({
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "upgrade",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "trailers",
    # Privacy — don't leak local network info to upstream
    "x-forwarded-for",
    "x-real-ip",
    "forwarded",
})

PROVIDER_URLS = {
    "anthropic": ANTHROPIC_BASE_URL,
    "openai": OPENAI_BASE_URL,
}


def detect_provider(path: str) -> str:
    """Detect upstream provider from request path."""
    if "/messages" in path:
        return "anthropic"
    if "/chat/completions" in path:
        return "openai"
    # Default based on common paths
    if "/models" in path:
        return "openai"
    return "anthropic"


def build_upstream_url(provider: str, path: str) -> str:
    """Build the full upstream URL."""
    base = PROVIDER_URLS.get(provider, ANTHROPIC_BASE_URL)
    return f"{base}{path}"


def clean_headers(raw_headers: dict[str, str]) -> dict[str, str]:
    """Remove hop-by-hop headers (RFC 7230) and Connection-listed headers."""
    # Headers named in the Connection header are also hop-by-hop
    connection_val = raw_headers.get("connection", "")
    extra_strip = {h.strip().lower() for h in connection_val.split(",") if h.strip()}
    strip = STRIP_HEADERS | extra_strip
    return {k: v for k, v in raw_headers.items() if k.lower() not in strip}


async def forward_request(client: httpx.AsyncClient, provider: str, path: str, headers: dict, body: bytes) -> httpx.Response:
    """Forward a non-streaming request and return the full response."""
    url = build_upstream_url(provider, path)
    cleaned = clean_headers(headers)

    response = await client.post(
        url,
        content=body,
        headers=cleaned,
    )
    return response


async def forward_stream(client: httpx.AsyncClient, provider: str, path: str, headers: dict, body: bytes):
    """Forward a streaming request. Returns an httpx stream context manager."""
    url = build_upstream_url(provider, path)
    cleaned = clean_headers(headers)

    return client.stream(
        "POST",
        url,
        content=body,
        headers=cleaned,
    )
