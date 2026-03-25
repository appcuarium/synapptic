"""Session browser — view all Claude Code conversations."""

import asyncio
import json
import os
from pathlib import Path

from fastapi import APIRouter, Body, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse

from synapptic.relay.routers import render_page

router = APIRouter()


def get_ws_clients(obj) -> set:
    """Get the shared ws_clients set from app state."""
    app = getattr(obj, 'app', obj)
    state = getattr(app, 'state', None)
    return getattr(state, 'ws_clients', set()) if state else set()


def get_store(obj):
    """Get the MetricsStore from app state. Accepts Request, WebSocket, or app."""
    app = getattr(obj, 'app', obj)
    state = getattr(app, 'state', None)
    return getattr(state, 'metrics_store', None) if state else None


def has_index(request: Request) -> bool:
    """Check if the session index has been populated."""
    store = get_store(request)
    if not store:
        return False
    try:
        return store.get_index_count() > 0
    except Exception:
        return False


@router.get("/")
async def browser_page():
    """Legacy /browser/ — serves the same unified dashboard."""
    return HTMLResponse(
        content=render_page("browser.html", active="dashboard"),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/api/projects")
async def api_projects(request: Request):
    """List all projects with session counts."""
    if has_index(request):
        store = get_store(request)
        projects = await asyncio.to_thread(store.get_indexed_projects)
        return JSONResponse(projects)
    # Fallback to file scan
    from synapptic.relay.parse import list_projects_with_sessions
    projects = await asyncio.to_thread(list_projects_with_sessions)
    return JSONResponse(projects)


@router.get("/api/sessions")
async def api_sessions(request: Request, project: str = Query(default=None)):
    """List sessions, optionally filtered by project slug."""
    if has_index(request):
        store = get_store(request)
        sessions = await asyncio.to_thread(store.get_indexed_sessions, project, 200)
        return JSONResponse(sessions)
    # Fallback to file scan
    from synapptic.relay.parse import list_all_sessions
    sessions = await asyncio.to_thread(list_all_sessions)
    if project:
        sessions = [s for s in sessions if s["project"] == project]
    return JSONResponse(sessions[:200])


@router.get("/api/search")
async def api_search(request: Request, q: str = Query(default="")):
    """Full-text search across session titles and first messages."""
    if not q or not has_index(request):
        return JSONResponse([])
    store = get_store(request)
    results = await asyncio.to_thread(store.search_sessions, q, 50)
    return JSONResponse(results)


@router.get("/api/session/{session_id}")
async def api_session(session_id: str, request: Request):
    """Get full conversation for a session."""
    from synapptic.config import CLAUDE_PROJECTS_DIR
    from synapptic.relay.parse import parse_session_full

    if not CLAUDE_PROJECTS_DIR.exists():
        return JSONResponse({"error": "No Claude projects found"}, status_code=404)

    jsonl_path = None
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.exists():
            jsonl_path = candidate
            break

    if jsonl_path:
        from synapptic.relay.parse import compute_session_stats
        turns = await asyncio.to_thread(parse_session_full, jsonl_path)
        stats = compute_session_stats(turns)
        return JSONResponse({"session_id": session_id, "turns": turns, "stats": stats})

    # Fallback: check relay DB for this session
    store = get_store(request)
    if store:
        session_data = await asyncio.to_thread(store.get_session, session_id)
        if session_data:
            turns = await asyncio.to_thread(store.get_session_turns, session_id)
            return JSONResponse({"session_id": session_id, "turns": turns})

    return JSONResponse({"error": "Session not found"}, status_code=404)


@router.websocket("/ws")
async def websocket_live(ws: WebSocket):
    """WebSocket for live session streaming and active session updates.

    Client sends: {"type": "watch", "session_id": "..."} to start tailing a session
    Client sends: {"type": "unwatch"} to stop tailing
    Server sends: {"type": "turn", "data": {...}} for new turns
    Server sends: {"type": "active", "sessions": [...]} every 10s with active session IDs
    """
    from synapptic.config import CLAUDE_PROJECTS_DIR
    from synapptic.relay.parse import parse_record, parse_session_full, compute_session_stats
    from synapptic.relay.config import MODEL_PRICING, DEFAULT_PRICING
    import time

    await ws.accept()
    clients = get_ws_clients(ws)
    clients.add(ws)

    watched_path = None
    watched_size = 0
    watched_session_id = None
    metrics_active = False
    # Running token totals for the currently watched session (updated as turns arrive)
    running_stats: dict = {}

    def _build_stats_from_turn(turn: dict, base: dict) -> dict:
        """Accumulate one assistant turn's usage into base stats dict."""
        usage = turn.get("usage") or {}
        if not usage:
            return base
        s = dict(base)
        s["total_requests"] = s.get("total_requests", 0) + 1
        s["total_input_tokens"] = s.get("total_input_tokens", 0) + (usage.get("input_tokens") or 0)
        s["total_output_tokens"] = s.get("total_output_tokens", 0) + (usage.get("output_tokens") or 0)
        s["total_cache_read_tokens"] = s.get("total_cache_read_tokens", 0) + (usage.get("cache_read") or 0)
        s["total_cache_create_tokens"] = s.get("total_cache_create_tokens", 0) + (usage.get("cache_create") or 0)
        model = turn.get("model") or ""
        pricing = MODEL_PRICING.get(model, DEFAULT_PRICING)
        inp = usage.get("input_tokens") or 0
        out = usage.get("output_tokens") or 0
        cr = usage.get("cache_read") or 0
        cost_delta = (inp / 1_000_000 * pricing["input"]) + (out / 1_000_000 * pricing["output"]) + (cr / 1_000_000 * pricing.get("cache_read", 0.30))
        s["estimated_cost_usd"] = round(s.get("estimated_cost_usd", 0.0) + cost_delta, 4)
        return s

    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=1.0)
                msg = json.loads(raw)

                if msg.get("type") == "watch":
                    # Switching sessions — always reset metrics state
                    watched_session_id = msg.get("session_id", "")
                    watched_path = None
                    metrics_active = False
                    running_stats = {}

                    if CLAUDE_PROJECTS_DIR.exists():
                        for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
                            candidate = project_dir / f"{watched_session_id}.jsonl"
                            if candidate.exists():
                                watched_path = candidate
                                watched_size = candidate.stat().st_size
                                break

                elif msg.get("type") == "unwatch":
                    watched_path = None
                    watched_session_id = None
                    metrics_active = False
                    running_stats = {}

                elif msg.get("type") == "metrics":
                    # Client requests live metrics for the currently watched JSONL session.
                    # Compute initial stats from the full file, then track increments as turns arrive.
                    if watched_path and watched_path.exists():
                        try:
                            turns = await asyncio.to_thread(parse_session_full, watched_path)
                            running_stats = compute_session_stats(turns)
                            metrics_active = True
                            await ws.send_json({"type": "metrics", "data": running_stats})
                        except Exception:
                            pass

            except asyncio.TimeoutError:
                pass

            # Tail watched file for new turns
            if watched_path:
                try:
                    current_size = watched_path.stat().st_size
                except FileNotFoundError:
                    watched_path = None
                    continue

                if current_size > watched_size:
                    with open(watched_path) as f:
                        f.seek(watched_size)
                        new_lines = f.readlines()
                    watched_size = current_size

                    stats_updated = False
                    for line in new_lines:
                        line = line.strip()
                        if not line:
                            continue
                        if '"type":"user"' not in line and '"type":"assistant"' not in line and '"type": "user"' not in line and '"type": "assistant"' not in line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if record.get("type") not in ("user", "assistant"):
                            continue
                        turn = parse_record(record, scrub=True)
                        if turn and turn.get("blocks"):
                            await ws.send_json({"type": "turn", "data": turn})
                            if metrics_active and turn.get("role") == "assistant" and turn.get("usage"):
                                running_stats = _build_stats_from_turn(turn, running_stats)
                                stats_updated = True

                    if metrics_active and stats_updated:
                        await ws.send_json({"type": "metrics", "data": running_stats})

    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        get_ws_clients(ws).discard(ws)


@router.post("/api/session-ended")
async def api_session_ended(request: Request, payload: dict = Body(default={})):
    """Called by the SessionEnd hook when a Claude Code session closes.

    Marks the JSONL session as ended so it no longer appears as active.
    Pushes a WebSocket notification to all connected browser clients.
    """
    session_id = payload.get("session_id", "")
    if not session_id:
        return JSONResponse({"ok": False, "error": "missing session_id"}, status_code=400)

    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    if state:
        ended = getattr(state, "ended_sessions", None)
        if ended is not None:
            ended.add(session_id)
        store = get_store(request)
        if store:
            try:
                store.add_ended_session(session_id)
            except Exception:
                pass

    # Push to all connected browser WebSocket clients
    clients = get_ws_clients(request)
    dead = set()
    for ws in list(clients):
        try:
            await ws.send_json({"type": "session_ended", "session_id": session_id})
        except Exception:
            dead.add(ws)
    clients.difference_update(dead)

    return JSONResponse({"ok": True})


@router.get("/api/active")
async def api_active_sessions(request: Request):
    """Find currently active sessions — both JSONL files and relay sessions."""
    from synapptic.config import CLAUDE_PROJECTS_DIR
    import time

    active = []
    cutoff = time.time() - 300  # 5 minutes

    # Sessions explicitly closed via SessionEnd hook
    app = getattr(request, "app", None)
    state = getattr(app, "state", None)
    ended = getattr(state, "ended_sessions", set()) if state else set()

    # 1. Active JSONL sessions (files modified in last 5 min, not yet ended)
    if CLAUDE_PROJECTS_DIR.exists():
        for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
            if not project_dir.is_dir():
                continue
            for jsonl_file in project_dir.glob("*.jsonl"):
                try:
                    sid = jsonl_file.stem
                    if jsonl_file.stat().st_mtime > cutoff and sid not in ended:
                        active.append({
                            "session_id": sid,
                            "source": "jsonl",
                            "modified": jsonl_file.stat().st_mtime,
                        })
                except OSError:
                    continue

    # 2. Active relay sessions (within last 5 minutes)
    store = get_store(request)
    if store:
        try:
            relay_sessions = await asyncio.to_thread(store.get_resumable_sessions, 5)
            for rs in relay_sessions:
                active.append({
                    "session_id": rs["id"],
                    "source": "relay",
                })
        except Exception:
            pass

    active.sort(key=lambda s: s.get("modified", 0), reverse=True)
    return JSONResponse(active[:20])


@router.get("/api/relay-sessions")
async def api_relay_sessions(request: Request):
    """List active relay sessions (within last 5 minutes)."""
    store = get_store(request)
    if not store:
        return JSONResponse([])
    try:
        sessions = await asyncio.to_thread(store.get_resumable_sessions, 5)
        # Enrich with full session data
        result = []
        for rs in sessions:
            full = await asyncio.to_thread(store.get_session, rs["id"])
            if full:
                result.append(full)
        return JSONResponse(result)
    except Exception:
        return JSONResponse([])
