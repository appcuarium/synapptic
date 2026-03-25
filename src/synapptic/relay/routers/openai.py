"""OpenAI API route handler — POST /v1/chat/completions."""

import asyncio
import hashlib
import json
import logging

from fastapi import APIRouter, Request
from fastapi.responses import Response, StreamingResponse

from synapptic.relay.context import RequestContext
from synapptic.relay.tokens import (
    extract_openai_usage_from_response,
    extract_openai_usage_from_sse,
    extract_request_metadata,
    summarize_messages,
)
from synapptic.relay.forwarder import forward_request, forward_stream
from synapptic.relay.sse import relay_sse_stream

logger = logging.getLogger("synapptic.openai")

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


@router.post("/chat/completions")
async def proxy_chat_completions(request: Request):
    """Forward OpenAI /v1/chat/completions requests, log metrics."""
    body_bytes = await request.body()
    body_json = json.loads(body_bytes)

    metadata = extract_request_metadata(body_json, "openai")
    is_stream = metadata["stream"]

    # Session tracking — use system prompt hash to distinguish projects
    tracker = _get_session_tracker(request)
    source_ip = request.client.host if request.client else "unknown"
    user_agent = request.headers.get("user-agent", "unknown")
    system_msgs = [m for m in body_json.get("messages", []) if m.get("role") == "system"]
    system_str = " ".join(m.get("content", "") for m in system_msgs)
    system_hash = hashlib.sha256(system_str.encode()).hexdigest()[:12] if system_str else ""
    session_id = tracker.get_or_create(source_ip, user_agent, system_hash)
    fingerprint = tracker.get_fingerprint(source_ip, user_agent)
    session_key = tracker.get_session_key(source_ip, user_agent, system_hash)

    store = request.app.state.metrics_store
    await store.async_upsert_session(session_id, fingerprint, session_key)

    request_messages = summarize_messages(body_json.get("messages", []), "openai")

    ctx = RequestContext(
        session_id=session_id,
        provider="openai",
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
        f"→ openai | model={ctx.model} | msgs={ctx.message_count} | "
        f"sys_tok≈{ctx.system_prompt_tokens} | tools={ctx.tool_definitions_count} | stream={is_stream}"
    )

    if is_stream:
        return await _handle_stream(client, headers, body_bytes, ctx, store)
    else:
        return await _handle_non_stream(client, headers, body_bytes, ctx, store)


async def _handle_non_stream(client, headers, body_bytes, ctx: RequestContext, store):
    """Forward non-streaming request, log usage from response."""
    response = await forward_request(client, "openai", "/v1/chat/completions", headers, body_bytes)

    try:
        resp_json = response.json()
        usage = extract_openai_usage_from_response(resp_json)
        ctx.input_tokens = usage["input_tokens"]
        ctx.output_tokens = usage["output_tokens"]
        choices = resp_json.get("choices", [])
        if choices:
            ctx.response_content = choices[0].get("message", {}).get("content", "") or ""
    except Exception:
        pass

    _log_completion(ctx)
    _spawn_task(store.async_insert_request(**ctx.to_store_kwargs()))

    resp_headers = {}
    for key in ("x-request-id", "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens"):
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
        try:
            usage = extract_openai_usage_from_sse(chunks)
            ctx.input_tokens = usage["input_tokens"]
            ctx.output_tokens = usage["output_tokens"]
        except Exception:
            pass
        _log_completion(ctx)
        await store.async_insert_request(**ctx.to_store_kwargs())

    stream_ctx = await forward_stream(client, "openai", "/v1/chat/completions", headers, body_bytes)

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
        f"← openai | model={ctx.model} | in={ctx.input_tokens} | out={ctx.output_tokens} | "
        f"latency={ctx.latency_ms:.0f}ms | session={ctx.session_id}"
    )
