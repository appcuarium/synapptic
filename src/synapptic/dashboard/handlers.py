"""Methods the browser can call over the websocket ({"type": "request", "method": ...}).

Each method takes the request params and the Hub, and returns a JSON-serialisable result or raises
MethodError. Config reads and writes go through providers.load_config / save_config (the same file the
CLI uses) on a worker thread. A stored API key never leaves the server: the browser gets api_key_set and
the last 4 characters.
"""

import asyncio
import time
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synapptic import providers
from synapptic.config import CLAUDE_PROJECTS_DIR
from synapptic.dashboard.hub import Hub
from synapptic.dashboard.protocol import ErrorCode
from synapptic.sessions.indexer import tail_metadata
from synapptic.scrub import scrub_text
from synapptic.sessions.parse import SHOWN_BLOCK_TYPES, compute_session_stats
from synapptic.sessions.store import DB_PATH, SessionStore
from synapptic.sessions.tail import TranscriptTail
from synapptic.state import include_headless_sessions, is_excluded_transcript, slug_from_project_dir

CONFIG_TOPIC = "config"
API_KEY_HINT_LENGTH = 4
SESSION_PAGE_SIZE = 100
SESSION_PAGE_MAX = 500
TOOL_SUMMARY_LENGTH = 160
# A live session: its transcript was written in the last 5 minutes and the SessionEnd hook has not reported
# it ended (POST /api/session-ended).
LIVE_WINDOW_SECONDS = 300
# The index columns the browser gets; file_path and project_dir stay on the server.
PUBLIC_SESSION_FIELDS = ("session_id", "project", "title", "model", "first_timestamp", "last_timestamp",
                         "message_count", "turn_count", "file_size")
# Incremental readers of the transcripts in use (live ones, the one on screen): path -> TranscriptTail, most
# recently used last. Reading a transcript once takes seconds for the largest; after that only new bytes are read.
TAIL_CACHE_SIZE = 8
parsed_session_cache: dict = {}


class MethodError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class BatchesPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    extract: bool | None = None


class ConfigPatch(BaseModel):
    """The settings the dashboard may change. Unknown keys are rejected."""
    model_config = ConfigDict(extra="forbid")
    provider: str | None = None
    model: str | None = Field(default=None, min_length=1, max_length=128)
    api_key: str | None = Field(default=None, max_length=512)   # write-only; "" clears it
    batches: BatchesPatch | None = None
    include_headless_sessions: bool | None = None


def public_config(config: dict) -> dict:
    """The config as the browser may see it: the API key replaced by whether it is set and its last 4."""
    key = str(config.get("api_key") or "")
    view = {name: value for name, value in config.items() if name != "api_key"}
    view["api_key_set"] = bool(key)
    view["api_key_hint"] = key[-API_KEY_HINT_LENGTH:] if len(key) > API_KEY_HINT_LENGTH else ""
    return view


def apply_patch(config: dict, patch: ConfigPatch) -> dict:
    """Return the config with the patch applied. Raises MethodError for an unknown provider."""
    updated = dict(config)
    if patch.provider is not None:
        if patch.provider not in providers.PROVIDERS:
            raise MethodError(ErrorCode.INVALID_PARAMS, f"unknown provider: {patch.provider}")
        updated["provider"] = patch.provider
    if patch.model is not None:
        updated["model"] = patch.model
    if patch.api_key is not None:
        if patch.api_key:
            updated["api_key"] = patch.api_key
        else:
            updated.pop("api_key", None)
    if patch.batches is not None and patch.batches.extract is not None:
        updated["batches"] = {**(updated.get("batches") or {}), "extract": patch.batches.extract}
    if patch.include_headless_sessions is not None:
        updated["include_headless_sessions"] = patch.include_headless_sessions
    return updated


def update_config_file(patch: ConfigPatch) -> dict:
    """Load, patch and save the config file. Runs on a worker thread."""
    updated = apply_patch(providers.load_config(), patch)
    providers.save_config(updated)
    return updated


async def config_get(params: dict, hub: Hub) -> dict:
    return public_config(await asyncio.to_thread(providers.load_config))


async def config_update(params: dict, hub: Hub) -> dict:
    try:
        patch = ConfigPatch.model_validate(params)
    except ValidationError as exc:
        raise MethodError(ErrorCode.INVALID_PARAMS, exc.errors(include_url=False)[0]["msg"]) from exc
    view = public_config(await asyncio.to_thread(update_config_file, patch))
    hub.publish(CONFIG_TOPIC, view)
    return view


async def providers_list(params: dict, hub: Hub) -> list[dict]:
    return [
        {
            "id": provider_id,
            "name": spec["name"],
            "requires_key": spec.get("requires_key", False),
            "default_model": spec.get("default_model", ""),
            "models": [{"id": model_id, "label": label} for model_id, label in spec.get("models", [])],
            "batches": providers.provider_supports(provider_id, "batches"),
        }
        for provider_id, spec in providers.PROVIDERS.items()
    ]


class SessionsListParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str | None = Field(default=None, max_length=256)


class SessionTurnsParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(min_length=1, max_length=64)
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=SESSION_PAGE_SIZE, ge=1, le=SESSION_PAGE_MAX)


def validated(model: type[BaseModel], params: dict) -> BaseModel:
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise MethodError(ErrorCode.INVALID_PARAMS, exc.errors(include_url=False)[0]["msg"]) from exc


def open_index() -> SessionStore:
    store = SessionStore(DB_PATH)
    store.init_db()
    return store


def public_session(row: dict) -> dict:
    return {name: row.get(name) for name in PUBLIC_SESSION_FIELDS}


def read_projects() -> list[dict]:
    store = open_index()
    try:
        return store.get_indexed_projects()
    finally:
        store.close()


def recent_transcripts(now: float) -> list[Path]:
    """The user's transcripts written to within the live window (scratchpads and, unless opted in, headless runs left out)."""
    if not CLAUDE_PROJECTS_DIR.is_dir():
        return []
    include_headless = include_headless_sessions(providers.load_config())
    recent = []
    for path in CLAUDE_PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            written = path.stat().st_mtime
        except OSError:
            continue
        if written > now - LIVE_WINDOW_SECONDS and not is_excluded_transcript(path, include_headless):
            recent.append(path)
    return recent


def index_transcript(store: SessionStore, path: Path) -> None:
    """Index a session from its transcript as read so far (new sessions are otherwise indexed when they end)."""
    tail = tail_for(path)
    with tail.lock:
        tail.update()
        meta = tail_metadata(tail)
    if not meta["message_count"]:
        return
    store.index_session(session_id=path.stem, project=slug_from_project_dir(path.parent.name), project_dir=path.parent.name,
                        title=meta["title"], model=meta["model"], first_ts=meta["first_timestamp"],
                        last_ts=meta["last_timestamp"], msg_count=meta["message_count"], file_size=tail.offset,
                        file_path=str(path), first_user_message=meta["first_user_message"],
                        turn_count=meta["turn_count"])


def tail_for(path: Path) -> TranscriptTail:
    """The shared incremental reader of a transcript (the most recently used TAIL_CACHE_SIZE are kept)."""
    # One reader per file whatever path names it (a symlinked projects folder, the index's stored path).
    # Raw text in memory: display_block scrubs only what is sent to the browser.
    real = path.resolve()
    key = str(real)
    tail = parsed_session_cache.pop(key, None) or TranscriptTail(real, scrub=False)
    parsed_session_cache[key] = tail
    while len(parsed_session_cache) > TAIL_CACHE_SIZE:
        parsed_session_cache.pop(next(iter(parsed_session_cache)))
    return tail


def read_sessions(project: str | None) -> list[dict]:
    """The index, with every live session brought up to its transcript's latest line (new bytes only)."""
    store = open_index()
    try:
        recent = recent_transcripts(time.time())
        for path in recent:
            index_transcript(store, path)
        live = {path.stem for path in recent} - store.get_ended_session_ids()
        return [{**public_session(row), "live": row["session_id"] in live}
                for row in store.get_indexed_sessions(project=project)]
    finally:
        store.close()


def record_session_ended(session_id: str) -> None:
    """The SessionEnd hook's report: the session is closed, whatever its transcript's last write."""
    store = open_index()
    try:
        store.add_ended_session(session_id)
    finally:
        store.close()


def read_session_row(session_id: str) -> dict | None:
    store = open_index()
    try:
        return store.get_indexed_session(session_id)
    finally:
        store.close()


def display_block(block: dict) -> dict | None:
    """The part of a block the page shows: text, a tool chip, a compaction marker. Thinking and tool output stay here.
    Secrets are scrubbed here, on what is sent, not when the transcript is read."""
    if block["type"] == "text":
        return {"type": "text", "text": scrub_text(block["text"])}
    if block["type"] == "tool_use":
        return {"type": "tool_use", "name": block["name"], "summary": scrub_text(block["input_summary"][:TOOL_SUMMARY_LENGTH])}
    if block["type"] == "compaction":
        return block
    return None


def display_turns(turns: list[dict]) -> list[dict]:
    shown = []
    for turn in turns:
        blocks = [view for view in (display_block(block) for block in turn["blocks"]) if view]
        if blocks:
            shown.append({"role": turn["role"], "timestamp": turn["timestamp"], "model": turn["model"],
                          "usage": turn["usage"], "blocks": blocks})
    return shown


def parsed_session(path: Path, offset: int, limit: int) -> tuple[list[dict], dict, int]:
    """(the shown turns offset..offset+limit, stats, number of shown turns) of a transcript read up to its latest
    line. Only new bytes are read, and only the requested turns are built for display."""
    tail = tail_for(path)
    with tail.lock:
        tail.update()
        shown = [turn for turn in tail.turns if any(block["type"] in SHOWN_BLOCK_TYPES for block in turn["blocks"])]
        return display_turns(shown[offset:offset + limit]), compute_session_stats(tail.turns), len(shown)


def read_session_turns(params: SessionTurnsParams) -> dict:
    row = read_session_row(params.session_id)
    if row is None:
        raise MethodError(ErrorCode.INVALID_PARAMS, f"unknown session: {params.session_id}")
    path = Path(row["file_path"] or "")
    if not path.is_file():
        raise MethodError(ErrorCode.INVALID_PARAMS, f"the transcript of {params.session_id} is no longer on disk")
    turns, stats, total = parsed_session(path, params.offset, params.limit)
    return {"session": public_session(row), "stats": stats, "total": total, "offset": params.offset, "turns": turns}


async def sessions_projects(params: dict, hub: Hub) -> list[dict]:
    return await asyncio.to_thread(read_projects)


async def sessions_list(params: dict, hub: Hub) -> list[dict]:
    query = validated(SessionsListParams, params)
    return await asyncio.to_thread(read_sessions, query.project)


async def sessions_turns(params: dict, hub: Hub) -> dict:
    return await asyncio.to_thread(read_session_turns, validated(SessionTurnsParams, params))


Method = Callable[[dict, Hub], Awaitable[object]]
METHODS: dict[str, Method] = {
    "config.get": config_get,
    "config.update": config_update,
    "providers.list": providers_list,
    "sessions.projects": sessions_projects,
    "sessions.list": sessions_list,
    "sessions.turns": sessions_turns,
}
