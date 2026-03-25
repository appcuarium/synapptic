"""Dashboard routes — HTML page + JSON API for metrics."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from synapptic.relay.config import DEFAULT_PRICING, MODEL_PRICING
from synapptic.relay.routers import render_page

router = APIRouter()


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a given model and token counts."""
    # Try exact match first, then prefix match
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        for key, p in MODEL_PRICING.items():
            if model.startswith(key):
                pricing = p
                break
    if not pricing:
        pricing = DEFAULT_PRICING

    return (input_tokens / 1_000_000 * pricing["input"]) + (output_tokens / 1_000_000 * pricing["output"])


@router.get("/", response_class=HTMLResponse)
async def dashboard_page():
    """Serve the unified dashboard — sessions + metrics in one page."""
    return HTMLResponse(
        content=render_page("browser.html", active="dashboard"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/stats")
async def dashboard_stats(request: Request):
    """Return JSON stats for the dashboard."""
    store = request.app.state.metrics_store
    tracker = getattr(request.app.state, "session_tracker", None)

    current_session_id = tracker.current_session_id if tracker else None

    # Get data from store (sync calls wrapped in to_thread)
    current_session = None
    recent_requests = []
    if current_session_id:
        current_session = await asyncio.to_thread(store.get_session, current_session_id)
        recent_requests = await asyncio.to_thread(store.get_recent_requests, current_session_id, 50)

    daily = await asyncio.to_thread(store.get_daily_totals)
    all_sessions = await asyncio.to_thread(store.get_all_sessions, 20)

    # Compute costs
    daily_cost = 0.0
    for req in recent_requests:
        daily_cost += _estimate_cost(req.get("model", ""), req.get("input_tokens", 0), req.get("output_tokens", 0))

    session_cost = 0.0
    if current_session:
        session_cost = _estimate_cost(
            "claude-sonnet-4-0",  # approximate — we'd need per-session model breakdown for exact
            current_session.get("total_input_tokens", 0),
            current_session.get("total_output_tokens", 0),
        )

    return JSONResponse({
        "current_session": {
            "id": current_session_id,
            "total_input_tokens": current_session.get("total_input_tokens", 0) if current_session else 0,
            "total_output_tokens": current_session.get("total_output_tokens", 0) if current_session else 0,
            "total_cache_read_tokens": current_session.get("total_cache_read_tokens", 0) if current_session else 0,
            "total_cache_creation_tokens": current_session.get("total_cache_creation_tokens", 0) if current_session else 0,
            "total_requests": current_session.get("total_requests", 0) if current_session else 0,
            "estimated_cost_usd": round(session_cost, 4),
        } if current_session_id else None,
        "recent_requests": [
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "model": r["model"],
                "input_tokens": r["input_tokens"],
                "output_tokens": r["output_tokens"],
                "cache_read_tokens": r["cache_read_tokens"],
                "cache_creation_tokens": r["cache_creation_tokens"],
                "message_count": r["message_count"],
                "latency_ms": round(r["latency_ms"], 0),
                "stream": bool(r["stream"]),
                "estimated_cost_usd": round(
                    _estimate_cost(r["model"], r["input_tokens"], r["output_tokens"]), 4
                ),
            }
            for r in recent_requests
        ],
        "daily_totals": {
            "input_tokens": daily["input_tokens"],
            "output_tokens": daily["output_tokens"],
            "cache_read_tokens": daily["cache_read_tokens"],
            "cache_creation_tokens": daily["cache_creation_tokens"],
            "request_count": daily["request_count"],
        },
        "sessions": [
            {
                "id": s["id"],
                "started_at": s["started_at"],
                "last_active": s["last_active"],
                "total_requests": s["total_requests"],
                "total_input_tokens": s["total_input_tokens"],
                "total_output_tokens": s["total_output_tokens"],
            }
            for s in all_sessions
        ],
    })


@router.post("/api/end-session")
async def end_session(request: Request):
    """Mark the most recent session as ended (called by synapptic run on exit)."""
    store = request.app.state.metrics_store
    try:
        sessions = await asyncio.to_thread(store.get_all_sessions, 1)
        if sessions:
            # Set last_active far in the past so it's no longer "resumable"
            await asyncio.to_thread(
                store._conn.execute,
                "UPDATE sessions SET last_active = '2000-01-01T00:00:00Z' WHERE id = ?",
                (sessions[0]["id"],),
            )
            await asyncio.to_thread(store._conn.commit)
        return JSONResponse({"ok": True})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/request/{request_id}")
async def request_detail(request_id: int, request: Request):
    """Return full request/response bodies for a single request."""
    store = request.app.state.metrics_store
    row = await asyncio.to_thread(store.get_request, request_id)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)

    import json
    messages = []
    try:
        messages = json.loads(row.get("request_messages") or "[]")
    except (json.JSONDecodeError, TypeError):
        pass

    return JSONResponse({
        "id": row["id"],
        "timestamp": row["timestamp"],
        "model": row["model"],
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "messages": messages,
        "response": row.get("response_content") or "",
    })


@router.get("/api/heartbeat")
async def heartbeat():
    """SSE stream — drops instantly when proxy dies, triggering dashboard window.close()."""
    async def beat():
        yield "retry: 500\n\n"
        while True:
            yield "data: ping\n\n"
            await asyncio.sleep(5)

    return StreamingResponse(beat(), media_type="text/event-stream")


@router.post("/api/shutdown")
async def shutdown():
    """Shut down the entire process group (proxy + shell script + claude). Called when dashboard tab closes."""
    import os
    import signal

    # Kill the whole process group — proxy, shell script, and the claude process it launched
    try:
        os.killpg(os.getpgid(os.getpid()), signal.SIGTERM)
    except ProcessLookupError:
        os.kill(os.getpid(), signal.SIGTERM)
    return JSONResponse({"status": "shutting down"})
