"""Methods the browser can call over the websocket ({"type": "request", "method": ...}).

Each method takes the request params and the Hub, and returns a JSON-serialisable result or raises
MethodError. Config reads and writes go through providers.load_config / save_config (the same file the
CLI uses) on a worker thread. A stored API key never leaves the server: the browser gets api_key_set and
the last 4 characters.
"""

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synapptic import harness, providers
from synapptic.config import CLAUDE_PROJECTS_DIR
from synapptic.dashboard import service
from synapptic.dashboard.hub import Hub
from synapptic.dashboard.protocol import ErrorCode
from synapptic.integrate import integrate_archetypes, rules_enabled, withdraw_rules
from synapptic.sessions.indexer import tail_metadata
from synapptic.scrub import scrub_text
from synapptic.sessions.parse import SHOWN_BLOCK_TYPES, compute_session_stats
from synapptic.sessions.store import DB_PATH, SessionStore, project_roots as indexed_project_roots
from synapptic.sessions.tail import TranscriptTail
from synapptic.state import include_headless_sessions, is_excluded_transcript, load_archetype, load_profile, slug_from_project_dir

CONFIG_TOPIC = "config"
GUARDS_TOPIC = "guards"
GUARD_CHANGES_SHOWN = 20
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
    write_rules: bool | None = None
    tuning: str | None = None                      # the dial: off, conservative, normal, eager


def public_config(config: dict) -> dict:
    """The config as the browser may see it: the API key replaced by whether it is set and its last 4."""
    key = str(config.get("api_key") or "")
    view = {name: value for name, value in config.items() if name != "api_key"}
    view["api_key_set"] = bool(key)
    view["api_key_hint"] = key[-API_KEY_HINT_LENGTH:] if len(key) > API_KEY_HINT_LENGTH else ""
    view["write_rules"] = rules_enabled(config)   # explicit: the file leaves it out until switched off
    view["tuning"] = harness.tuning_level(config)  # explicit, for the same reason
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
    if patch.write_rules is not None:
        updated["write_rules"] = patch.write_rules
    if patch.tuning is not None:
        if patch.tuning not in harness.TUNING_LEVELS:
            raise MethodError(ErrorCode.INVALID_PARAMS, f"unknown tuning level: {patch.tuning}")
        updated["harness"] = {**(updated.get("harness") or {}), "tuning": patch.tuning}
    return updated


def update_config_file(patch: ConfigPatch) -> dict:
    """Load, patch and save the config file. Runs on a worker thread. Switching the rules deploys them into the
    projects (on) or takes them out (off), as `synapptic config rules on|off` does."""
    config = providers.load_config()
    updated = apply_patch(config, patch)
    providers.save_config(updated)
    if rules_enabled(updated) != rules_enabled(config):
        if rules_enabled(updated):
            integrate_archetypes()
        else:
            withdraw_rules()
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


# ----- the guards: the rules the agent runs on, their numbers over the window, the changes, the undo ----------
# The same operations as `synapptic guards`, `synapptic stats`, `synapptic diff` and `synapptic rollback`.
# Every write pushes the new list to the "guards" topic, as config writes do.

class GuardSwitchParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    off: bool


class GuardUndoParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=32)


EMPTY_NUMBERS = {"fires": 0, "idle": 0, "misses": 0, "chars": 0, "sessions": 0, "daily": []}


def guard_view(rule: dict, numbers: dict | None) -> dict:
    """One rule as the browser shows it: the plain export's fields by their labels, plus its numbers. The
    patterns a rule listens for stay out of the page."""
    before = rule.get("before") or {}
    family = rule.get("family") or {}
    return {
        "id": rule["rule"],
        "off": bool(rule.get("switched off")),
        "where": rule.get("applies under") or "",
        "context": before.get("context") or "",
        "message": rule.get("message") or "",
        "family": bool(family),
        "anchor": family.get("anchor") or "",
        "why": rule.get("why") or "",
        "numbers": {name: numbers[name] for name in EMPTY_NUMBERS} if numbers else dict(EMPTY_NUMBERS),
    }


def project_roots() -> dict[str, str]:
    """Each indexed project's directory on disk, by slug (the index's lookup, on this module's index path)."""
    return indexed_project_roots(DB_PATH)


def rule_applies_under(where: str, root: str) -> bool:
    try:
        return re.search(where, root) is not None
    except re.error:
        return False


def read_guards() -> dict:
    """The rules and the projects they apply to, without their numbers (those come from guards.numbers, so the
    page shows the rules at once): {projects: [{project, label, rules}], rules: [guard_view + projects: [slug...]
    (empty: everywhere)]}."""
    core = harness.require_core()
    export = json.loads(core.harness_export())
    views = [guard_view(rule, None) for rule in export["rules"]]
    for view in views:
        del view["numbers"]
    roots = project_roots()
    for view in views:
        view["projects"] = [slug for slug, root in roots.items() if view["where"] and rule_applies_under(view["where"], root)]
    projects = [{"project": "", "label": "Global", "rules": sum(1 for view in views if not view["where"])}]
    for row in read_projects():
        slug = row["project"]
        projects.append({"project": slug, "label": slug, "rules": sum(1 for view in views if slug in view["projects"])})
    return {"projects": projects, "rules": views}


def read_guard_numbers() -> dict:
    """The rules' numbers over the window, by rule id: {days, events, transcripts, numbers: {id: {fires, idle,
    misses, chars, sessions, daily}}}. The slow half of the page, asked for after the rules are on screen."""
    found = harness.stats(harness.RULE_WINDOW_DAYS)
    numbers = {rule["id"]: {name: rule[name] for name in EMPTY_NUMBERS} for rule in found["rules"]}
    return {"days": found["days"], "events": found["events"], "transcripts": found["transcripts"], "numbers": numbers}


def alternatives(pattern: str) -> list[str]:
    """The top-level alternatives of a pattern (split on | outside groups), to count what a change added or took."""
    parts, depth, current, i = [], 0, "", 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "\\" and i + 1 < len(pattern):
            current += pattern[i:i + 2]
            i += 2
            continue
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        if ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
        i += 1
    parts.append(current)
    return [part for part in parts if part]


def change_view(change: dict) -> dict:
    """A change to a rule's words without the words: what kind, which rule, when, the numbers, how many
    alternatives came and went, and whether it was undone."""
    before, after = alternatives(change.get("before") or ""), alternatives(change.get("after") or "")
    return {"id": change["id"], "ts": change["ts"], "rule_id": change["rule_id"], "kind": change["kind"],
            "key": change.get("key", ""), "numbers": change.get("numbers"), "undone": change.get("undone"),
            "added": sum(1 for part in after if part not in before), "removed": sum(1 for part in before if part not in after)}


def read_changes() -> list[dict]:
    return [change_view(change) for change in harness.changes(GUARD_CHANGES_SHOWN)]


def twin_text(archetype: str) -> str:
    """The archetype's twin: its text up to the Guards section (the guards are the rules the page lists)."""
    cut = re.search(r"^## Guards\b", archetype, re.M)
    return archetype[:cut.start()].rstrip() if cut else archetype


def read_profile(project: str) -> dict:
    """The user's twin for a project (the global one for ""): the archetype's twin text and the profile's dimensions."""
    slug = project or None
    profile = load_profile(slug)
    dimensions = [{"name": name, "count": len(prefs or [])} for name, prefs in (profile.get("dimensions") or {}).items()]
    return {"project": project, "archetype": twin_text(load_archetype(slug) or ""), "dimensions": dimensions,
            "version": (profile.get("metadata") or {}).get("profile_version", 0)}


def guarded(call, *args):
    """Run a guards operation on a worker thread; the core's refusals and its absence become method errors."""
    try:
        return call(*args)
    except (harness.HarnessError, ValueError) as exc:
        raise MethodError(ErrorCode.INVALID_PARAMS, str(exc)) from exc


async def guards_list(params: dict, hub: Hub) -> dict:
    return await asyncio.to_thread(guarded, read_guards)


async def guards_numbers(params: dict, hub: Hub) -> dict:
    return await asyncio.to_thread(guarded, read_guard_numbers)


async def guards_changes(params: dict, hub: Hub) -> list[dict]:
    return await asyncio.to_thread(guarded, read_changes)


class ProfileParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(default="", max_length=256)


async def profile_get(params: dict, hub: Hub) -> dict:
    query = validated(ProfileParams, params)
    return await asyncio.to_thread(read_profile, query.project)


# ----- the server itself ------------------------------------------------------------------------------
# The page's restart button: the command that restarts this server where it runs, set by the command that
# started it (`synapptic dashboard serve`, or another dashboard's), spawned detached so it outlives the
# server it stops. The page polls /api/health and reloads when the server is back.

RESTART: dict = {"command": None, "log": None}


def spawn_restart() -> dict:
    if not RESTART["command"]:
        raise MethodError(ErrorCode.INVALID_PARAMS, "this server was not started by a synapptic command; restart it where it runs")
    service.spawn_detached(RESTART["command"], Path(RESTART["log"]))
    return {"restarting": True}


async def server_restart(params: dict, hub: Hub) -> dict:
    return await asyncio.to_thread(spawn_restart)


async def guards_switch(params: dict, hub: Hub) -> dict:
    query = validated(GuardSwitchParams, params)
    await asyncio.to_thread(guarded, harness.switch_rule, query.id, query.off)
    view = await asyncio.to_thread(guarded, read_guards)
    hub.publish(GUARDS_TOPIC, view)
    return view


async def guards_undo(params: dict, hub: Hub) -> dict:
    query = validated(GuardUndoParams, params)
    done = await asyncio.to_thread(guarded, harness.undo, query.id)
    hub.publish(GUARDS_TOPIC, await asyncio.to_thread(guarded, read_guards))
    return done


Method = Callable[[dict, Hub], Awaitable[object]]
METHODS: dict[str, Method] = {
    "config.get": config_get,
    "config.update": config_update,
    "providers.list": providers_list,
    "sessions.projects": sessions_projects,
    "sessions.list": sessions_list,
    "sessions.turns": sessions_turns,
    "guards.list": guards_list,
    "guards.numbers": guards_numbers,
    "guards.changes": guards_changes,
    "guards.switch": guards_switch,
    "guards.undo": guards_undo,
    "profile.get": profile_get,
    "server.restart": server_restart,
}
