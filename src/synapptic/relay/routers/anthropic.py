"""Anthropic API route handler — POST /v1/messages."""

import asyncio
import hashlib
import json
import logging
import time

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from synapptic.relay.context import RequestContext
from synapptic.relay.tokens import (
    extract_anthropic_response_text_from_sse,
    extract_anthropic_usage_from_response,
    extract_anthropic_usage_from_sse,
    extract_request_metadata,
    summarize_messages,
)
from synapptic.relay.forwarder import forward_request, forward_stream
from synapptic.relay.sse import relay_sse_stream

logger = logging.getLogger("synapptic.anthropic")

router = APIRouter()

# Strong references to background tasks — prevents GC before completion (Python 3.12+)
_background_tasks: set[asyncio.Task] = set()


def _get_session_tracker(request: Request):
    """Lazy-init session tracker on app state, restoring recent sessions from SQLite."""
    if not hasattr(request.app.state, "session_tracker"):
        from synapptic.relay.sessions import SessionTracker
        tracker = SessionTracker()
        tracker.load_from_store(request.app.state.metrics_store)
        request.app.state.session_tracker = tracker
    return request.app.state.session_tracker


@router.post("/messages")
async def proxy_messages(request: Request):
    """Forward Anthropic /v1/messages requests, log metrics."""
    body_bytes = await request.body()
    body_json = json.loads(body_bytes)

    # Pre-request metadata
    metadata = extract_request_metadata(body_json, "anthropic")
    is_stream = metadata["stream"]

    # Session tracking — use system prompt hash to distinguish projects
    tracker = _get_session_tracker(request)
    source_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    system_raw = body_json.get("system", "")
    system_str = system_raw if isinstance(system_raw, str) else json.dumps(system_raw)
    system_hash = hashlib.sha256(system_str.encode()).hexdigest()[:12] if system_str else ""
    session_id, is_new_session = tracker.get_or_create(source_ip, user_agent, system_hash)
    fingerprint = tracker.get_fingerprint(source_ip, user_agent)
    session_key = tracker.get_session_key(source_ip, user_agent, system_hash)

    store = request.app.state.metrics_store
    await store.async_upsert_session(session_id, fingerprint, session_key)

    # Notify browser clients immediately on new session — but only if user has indexed sessions
    if is_new_session:
        try:
            from synapptic.relay.routers.browser import get_ws_clients
            index_ready = store.get_index_count() > 0
            if index_ready:
                clients = get_ws_clients(request)
                dead = set()
                for ws_client in list(clients):
                    try:
                        await ws_client.send_json({"type": "active_check"})
                    except Exception:
                        dead.add(ws_client)
                clients.difference_update(dead)
        except Exception:
            pass

    # Capture request messages summary
    request_messages = summarize_messages(body_json.get("messages", []), "anthropic")

    # Build request context
    ctx = RequestContext(
        session_id=session_id,
        provider="anthropic",
        model=metadata["model"],
        message_count=metadata["message_count"],
        system_prompt_tokens=metadata["system_prompt_tokens"],
        tool_definitions_count=metadata["tool_definitions_count"],
        stream=is_stream,
        request_messages=request_messages,
    )

    headers = dict(request.headers)
    client = request.app.state.http_client

    logger.info(
        f"→ anthropic | model={ctx.model} | msgs={ctx.message_count} | "
        f"sys_tok≈{ctx.system_prompt_tokens} | tools={ctx.tool_definitions_count} | stream={is_stream}"
    )

    if is_stream:
        return await _handle_stream(client, headers, body_bytes, ctx, store)
    else:
        return await _handle_non_stream(client, headers, body_bytes, ctx, store)


async def _handle_non_stream(client, headers, body_bytes, ctx: RequestContext, store):
    """Forward non-streaming request, log usage from response."""
    response = await forward_request(client, "anthropic", "/v1/messages", headers, body_bytes)

    try:
        resp_json = response.json()
        usage = extract_anthropic_usage_from_response(resp_json)
        ctx.input_tokens = usage["input_tokens"]
        ctx.output_tokens = usage["output_tokens"]
        ctx.cache_read_tokens = usage["cache_read_tokens"]
        ctx.cache_creation_tokens = usage["cache_creation_tokens"]
        # Extract response text
        content_blocks = resp_json.get("content", [])
        text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
        ctx.response_content = " ".join(text_parts)
    except Exception:
        pass

    _log_completion(ctx)
    _spawn_task(store.async_insert_request(**ctx.to_store_kwargs()))

    # Forward response headers that matter
    resp_headers = {}
    for key in ("x-request-id", "request-id", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens"):
        if key in response.headers:
            resp_headers[key] = response.headers[key]

    return Response(
        content=response.content,
        status_code=response.status_code,
        headers=resp_headers,
        media_type=response.headers.get("content-type", "application/json"),
    )


async def _handle_stream(client, headers, body_bytes, ctx: RequestContext, store):
    """Forward streaming request with SSE passthrough."""

    async def on_stream_complete(chunks: list[bytes], resp_headers: dict):
        """Post-process accumulated SSE chunks after stream ends."""
        try:
            usage = extract_anthropic_usage_from_sse(chunks)
            ctx.input_tokens = usage["input_tokens"]
            ctx.output_tokens = usage["output_tokens"]
            ctx.cache_read_tokens = usage["cache_read_tokens"]
            ctx.cache_creation_tokens = usage["cache_creation_tokens"]
            ctx.response_content = extract_anthropic_response_text_from_sse(chunks)
        except Exception:
            pass
        _log_completion(ctx)
        await store.async_insert_request(**ctx.to_store_kwargs())

    stream_ctx = await forward_stream(client, "anthropic", "/v1/messages", headers, body_bytes)

    async def generate():
        async with stream_ctx as response:
            resp_headers = dict(response.headers)
            async for chunk in relay_sse_stream(response, on_stream_complete, resp_headers):
                yield chunk

    return StreamingResponse(generate(), media_type="text/event-stream")


def _spawn_task(coro):
    """Create a background task with a strong reference to prevent GC."""
    task = asyncio.create_task(coro)
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _log_completion(ctx: RequestContext):
    """Log request completion."""
    logger.info(
        f"← anthropic | model={ctx.model} | in={ctx.input_tokens} | out={ctx.output_tokens} | "
        f"cache_read={ctx.cache_read_tokens} | cache_create={ctx.cache_creation_tokens} | "
        f"latency={ctx.latency_ms:.0f}ms | session={ctx.session_id}"
    )
