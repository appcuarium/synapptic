"""Methods the browser can call over the websocket ({"type": "request", "method": ...}).

Each method takes the request params and the Hub, and returns a JSON-serialisable result or raises
MethodError. Config reads and writes go through providers.load_config / save_config (the same file the
CLI uses) on a worker thread. A stored API key never leaves the server: the browser gets api_key_set and
the last 4 characters.
"""

import asyncio
import functools
import json
import re
import threading
import time
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synapptic import benchmark, harness, providers
from synapptic.core import CoreMissing
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
KEYWORD_MAX_LENGTH = 16   # the word that turns a prompt into a rule: one word, this long at most
CUSTOM_PROVIDER = "custom"   # the provider whose saved endpoints become cards of their own
SESSION_PAGE_SIZE = 100
SESSION_PAGE_MAX = 500
TOOL_SUMMARY_LENGTH = 160
# A live session: its transcript was written in the last 5 minutes (operator 2026-11-05: activity, not an open
# process; a process-based lookup was built that morning and taken out the same evening, since it guessed
# the project's newest session for a title it could not match) and the SessionEnd hook has not reported it
# ended (POST /api/session-ended).
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
    api_url: str | None = Field(default=None, max_length=512)   # the endpoint's URL (custom, a local server, an API); "" clears it
    endpoint_name: str | None = Field(default=None, max_length=64)   # a custom endpoint's name: it becomes a card of its own
    remove_endpoint: str | None = Field(default=None, max_length=512)   # the URL of a saved custom endpoint to drop (its card goes)
    batches: BatchesPatch | None = None
    include_headless_sessions: bool | None = None
    write_rules: bool | None = None
    tuning: str | None = None                      # the dial: off, conservative, normal, eager
    keyword: str | None = Field(default=None, min_length=1, max_length=KEYWORD_MAX_LENGTH)   # the word that turns a prompt into a rule


def rule_keyword() -> str | None:
    """The word that turns a prompt into a rule, kept with the rules; None without the core (the page then
    shows no field)."""
    try:
        return harness.keyword()
    except (CoreMissing, harness.HarnessError):
        return None


def public_config(config: dict) -> dict:
    """The config as the browser may see it: the API key replaced by whether it is set and its last 4."""
    key = str(config.get("api_key") or "")
    view = {name: value for name, value in config.items() if name != "api_key"}
    view["api_key_set"] = bool(key)
    view["api_key_hint"] = key[-API_KEY_HINT_LENGTH:] if len(key) > API_KEY_HINT_LENGTH else ""
    view["write_rules"] = rules_enabled(config)   # explicit: the file leaves it out until switched off
    view["tuning"] = harness.tuning_level(config)  # explicit, for the same reason
    keyword = rule_keyword()
    if keyword is not None:
        view["keyword"] = keyword
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
    if patch.api_url is not None:
        url = patch.api_url.strip()
        if url and not url.startswith(("http://", "https://")):
            raise MethodError(ErrorCode.INVALID_PARAMS, "the API URL starts with http:// or https://")
        if url:
            updated["api_url"] = url
        else:
            updated.pop("api_url", None)
    if updated.get("provider") == CUSTOM_PROVIDER and updated.get("api_url"):
        # a saved custom endpoint is a card of its own (operator 2026-11-06): one entry per URL, named by
        # the user (or after its host), its model the one saved with it; a save with the same URL updates it
        entries = [dict(e) for e in (updated.get("endpoints") or []) if isinstance(e, dict) and e.get("url")]
        known = next((e for e in entries if e["url"] == updated["api_url"]), None)
        name = (patch.endpoint_name or "").strip() or (known or {}).get("name") or urlparse(updated["api_url"]).netloc or updated["api_url"]
        entry = {"name": name, "url": updated["api_url"], "model": updated.get("model") or ""}
        if known:
            known.update(entry)
        else:
            entries.append(entry)
        updated["endpoints"] = entries
    if patch.remove_endpoint:
        # a saved endpoint is dropped with its card (operator 2026-11-06: "all info about the custom endpoint
        # goes"); when it was the one in use, its URL, model and key go with it
        updated["endpoints"] = [e for e in (updated.get("endpoints") or []) if not (isinstance(e, dict) and e.get("url") == patch.remove_endpoint)]
        if updated.get("api_url") == patch.remove_endpoint:
            updated.pop("api_url", None)
            updated.pop("api_key", None)
            updated["model"] = ""
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
    if patch.keyword is not None:
        try:
            harness.set_keyword(patch.keyword)   # kept with the rules, not in the file; refused before anything is saved
        except (CoreMissing, harness.HarnessError) as exc:
            raise MethodError(ErrorCode.INVALID_PARAMS, str(exc)) from exc
    providers.save_config(updated)
    if rules_enabled(updated) != rules_enabled(config):
        if rules_enabled(updated):
            integrate_archetypes()
        else:
            withdraw_rules()
    return updated


def config_view() -> dict:
    """The config file as the browser sees it; on a worker thread, as the keyword comes from the rules' store."""
    return public_config(providers.load_config())


def updated_config_view(patch: ConfigPatch) -> dict:
    return public_config(update_config_file(patch))


async def config_get(params: dict, hub: Hub) -> dict:
    return await asyncio.to_thread(config_view)


async def config_update(params: dict, hub: Hub) -> dict:
    try:
        patch = ConfigPatch.model_validate(params)
    except ValidationError as exc:
        raise MethodError(ErrorCode.INVALID_PARAMS, exc.errors(include_url=False)[0]["msg"]) from exc
    view = await asyncio.to_thread(updated_config_view, patch)
    hub.publish(CONFIG_TOPIC, view)
    return view


async def providers_list(params: dict, hub: Hub) -> list[dict]:
    return [
        {
            "id": provider_id,
            "name": spec["name"],
            "requires_key": spec.get("requires_key", False),
            "requires_url": spec.get("requires_url", False),      # the custom endpoint: no URL, no provider
            "default_url": spec.get("default_url", ""),           # a local server or an API with a known URL
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


def benchmark_history(value) -> list:
    """The rule's benchmark verdicts as a list, newest first (a verdict stored alone before the history was kept
    is a list of one)."""
    if isinstance(value, list):
        return value
    return [value] if isinstance(value, dict) else []


RULE_BY_HAND = "hand"   # a rule's origin when the store records none: written with `guards add`


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
        "origin": rule.get("origin") or RULE_BY_HAND,   # where it came from: synthesis, keyword (a RULE: prompt), hand
        "benchmark": benchmark_history(rule.get("benchmark")),   # the rule's benchmark verdicts, newest first
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
        view["benchmark_running"] = view["id"] in benchmark_jobs   # a page opened mid-run opens the run's box
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
    # a restart kills the benchmark jobs with the server (an opus run was lost that way, 2026-11-05)
    if benchmark_jobs:
        raise MethodError(ErrorCode.INVALID_PARAMS, f"a benchmark is running on {', '.join(sorted(benchmark_jobs))}; stop it or wait for it first")
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


# ----- the benchmark button on a rule ----------------------------------------------------------------
# One rule, the rule on against the rule off through the harness (benchmark.run_rule_benchmark): a job in the
# background, its steps pushed on the "benchmark" topic as {rule, message} and its end as {rule, done,
# verdict} (or {rule, error}), then the rules list pushed again with the verdict on the rule. The respondent
# is a headless Claude Code session; the judge is the configured provider and model (Settings).

BENCHMARK_TOPIC = "benchmark"
benchmark_jobs: dict[str, asyncio.Task] = {}   # rule id -> the running job
benchmark_cancels: dict[str, threading.Event] = {}   # rule id -> set by guards.benchmark.stop; the job stops at its next arm
# rule id -> the events of its latest run so far (a page opened or reloaded mid-run asks for them:
# guards.benchmark.state), kept after the run ends until the next one starts
benchmark_events: dict[str, list[dict]] = {}


class GuardBenchmarkParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    runs: int = Field(default=benchmark.RULE_BENCHMARK_RUNS, ge=1, le=9)
    runner_provider: str = Field(default="", max_length=64)    # the runner: Claude Code (the hooks run there) or any provider
    runner_model: str = Field(default="", max_length=128)      # the runner's model (Claude Code's: sonnet, opus, fable, haiku)
    judge_provider: str = Field(default="", max_length=64)     # the judge, as the CLI's --judge-provider / --judge-model
    judge_model: str = Field(default="", max_length=128)


def benchmark_cwd(rule_id: str) -> str | None:
    """Where the headless session runs: a project the rule applies under, else wherever the server runs."""
    for rule in read_guards()["rules"]:
        if rule["id"] == rule_id and rule["where"]:
            roots = project_roots()
            return next((roots[slug] for slug in rule["projects"] if slug in roots), None)
    return None


def run_benchmark_job(query: GuardBenchmarkParams, report) -> dict:
    """The job itself, in a worker thread: `report(event)` hears every step. The judge is the configured
    provider and model unless the page chose another (the CLI's --judge-provider / --judge-model); the
    runner is the chosen provider and model (Claude Code by default)."""
    config = providers.load_config()
    judge_config = dict(config)
    if query.judge_provider:
        judge_config["provider"] = query.judge_provider
    if query.judge_model:
        judge_config["model"] = query.judge_model
    return benchmark.run_rule_benchmark(query.id, config=config, judge_config=judge_config, runs=query.runs,
                                        cwd=benchmark_cwd(query.id), progress=report, model=query.runner_model or None,
                                        provider=query.runner_provider or None, cancel=benchmark_cancels.get(query.id))


def publish_progress(loop: asyncio.AbstractEventLoop, hub: Hub, rule_id: str, event: dict) -> None:
    """A step of the job, from its worker thread onto the topic (the hub is the loop's): the event as the
    benchmark reports it (kind note, scenario, reply or judge) with the rule's id."""
    shown = {"rule": rule_id, **event}
    benchmark_events.setdefault(rule_id, []).append(shown)
    loop.call_soon_threadsafe(hub.publish, BENCHMARK_TOPIC, shown)


async def benchmark_job(query: GuardBenchmarkParams, hub: Hub) -> None:
    rule_id = query.id
    report = functools.partial(publish_progress, asyncio.get_running_loop(), hub, rule_id)
    try:
        verdict = await asyncio.to_thread(guarded, run_benchmark_job, query, report)
        hub.publish(BENCHMARK_TOPIC, {"rule": rule_id, "done": True, "verdict": verdict})
        hub.publish(GUARDS_TOPIC, await asyncio.to_thread(guarded, read_guards))
    except MethodError as failure:
        hub.publish(BENCHMARK_TOPIC, {"rule": rule_id, "error": failure.message})
    except Exception as failure:  # noqa: BLE001 - the job must end on the topic whatever broke (the page waits on it)
        hub.publish(BENCHMARK_TOPIC, {"rule": rule_id, "error": f"{type(failure).__name__}: {failure}"})
    finally:
        benchmark_jobs.pop(rule_id, None)
        benchmark_cancels.pop(rule_id, None)


async def guards_benchmark(params: dict, hub: Hub) -> dict:
    query = validated(GuardBenchmarkParams, params)
    if query.id in benchmark_jobs:
        return {"rule": query.id, "started": False, "running": True}
    benchmark_cancels[query.id] = threading.Event()
    benchmark_events[query.id] = []
    benchmark_jobs[query.id] = asyncio.create_task(benchmark_job(query, hub))
    return {"rule": query.id, "started": True, "running": True}


async def guards_benchmark_running(params: dict, hub: Hub) -> dict:
    """The rules whose benchmark is running right now (a restart would kill them)."""
    return {"rules": sorted(benchmark_jobs)}


# The Benchmark page (operator 2026-11-04): the one-rule engine over every rule under the strip's card, with
# the CLI's choices, one rule after another on one job. Its events go on the benchmark topic under each rule's
# id (the rule's own box on Profile & Guards follows them too) and under SCOPE_JOB for the page.
SCOPE_JOB = "__scope__"
ALL_PROJECTS = "__all__"
SCOPE_LIMIT_DEFAULT = 10
SCOPE_LIMIT_MAX = 50
SCOPE_SEED_MAX = 999_999
SCOPE_TEMPERATURE_DEFAULT = 0.1   # the previous CLI's default for the runner


class GuardBenchmarkScopeParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(default="", max_length=256)              # "" Global, ALL_PROJECTS every rule, else a slug
    limit: int = Field(default=SCOPE_LIMIT_DEFAULT, ge=1, le=SCOPE_LIMIT_MAX)   # the CLI's number of rules
    runs: int = Field(default=benchmark.RULE_BENCHMARK_RUNS, ge=1, le=9)
    runner_provider: str = Field(default="", max_length=64)
    runner_model: str = Field(default="", max_length=128)
    judge_provider: str = Field(default="", max_length=64)
    judge_model: str = Field(default="", max_length=128)
    seed: int = Field(default=0, ge=0, le=SCOPE_SEED_MAX)          # the CLI's --seed: which rules, and the scenarios kept for them
    refresh: bool = False                                          # the CLI's --refresh: generate the scenarios again
    temperature: float = Field(default=SCOPE_TEMPERATURE_DEFAULT, ge=0, le=2)   # the CLI's --temperature for the runner


def rule_in_scope(rule: dict, project: str) -> bool:
    """The page's own filter: the All card takes every rule, Global the rules with no directory, a project
    the rules under it. Synapptic's own rules are never benchmarked here."""
    if rule.get("core"):
        return False
    if project == ALL_PROJECTS:
        return True
    if project == "":
        return not rule["projects"] and not rule["where"]
    return project in rule["projects"]


def scope_rules(project: str, limit: int) -> list[dict]:
    return [rule for rule in read_guards()["rules"] if rule_in_scope(rule, project)][:limit]


def run_scope_job(query: GuardBenchmarkScopeParams, report, cancel: threading.Event) -> dict:
    """The job itself, in a worker thread, the previous CLI's full benchmark on the rules (operator 2026-11-05):
    the rules in scope selected by the seed, one scenario per rule generated once and kept for that seed (so
    both arms, every run and every rerun face the same cases), the two control cases read by the judge, then
    each rule through the one-rule benchmark on its kept scenario with the page's choices (`report(rule id,
    event)` hears every step, `report(rule id, {done, verdict})` each rule's end; the run's own notes come
    under the scope id), the rule's own switch put back after it; stopped at the next rule when the user says so."""
    config = providers.load_config()
    judge_config = dict(config)
    if query.judge_provider:
        judge_config["provider"] = query.judge_provider
    if query.judge_model:
        judge_config["model"] = query.judge_model
    provider = query.runner_provider or benchmark.RULE_RESPONDENT_PROVIDER
    model = query.runner_model or benchmark.respondent_model(config, provider)
    say = functools.partial(report, SCOPE_JOB)
    rules = benchmark.select_rules(scope_rules(query.project, SCOPE_LIMIT_MAX), query.limit, query.seed)
    scenarios = benchmark.rule_scenarios(rules, query.project, query.seed, config, refresh=query.refresh, progress=say, cancel=cancel)
    controls = None
    if not cancel.is_set():
        say({"kind": "note", "message": "reading the judge on the two control cases"})
        controls = benchmark.judge_controls(judge_config, benchmark.runner_for(provider, config, query.temperature), model,
                                            benchmark_cwd(rules[0]["id"]) if rules else None, progress=say)
    verdicts = []
    for rule in rules:
        if cancel.is_set():
            break
        rule_id = rule["id"]
        if rule_id not in scenarios:
            verdicts.append(benchmark.untestable_verdict(rule_id, "no scenario came back from the generation model"))
            report(rule_id, {"done": True, "verdict": verdicts[-1]})
            continue
        benchmark_jobs[rule_id] = benchmark_jobs[SCOPE_JOB]   # the rule's box opens on Profile & Guards
        benchmark_cancels[rule_id] = cancel
        try:
            verdict = benchmark.run_rule_benchmark(rule_id, config=config, judge_config=judge_config, runs=query.runs,
                                                   temperature=query.temperature, cwd=benchmark_cwd(rule_id),
                                                   progress=functools.partial(report, rule_id), scenario=scenarios[rule_id],
                                                   model=query.runner_model or None, provider=query.runner_provider or None,
                                                   cancel=cancel, was_off=rule["off"])
        finally:
            benchmark_jobs.pop(rule_id, None)
            benchmark_cancels.pop(rule_id, None)
        verdicts.append(verdict)
        report(rule_id, {"done": True, "verdict": verdict})
    run = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "project": query.project,
        "choices": query.model_dump(exclude={"project"}),
        "rules": len(rules),
        "selected": [rule["id"] for rule in rules],
        "controls": controls,
        "verdicts": verdicts,
        "stopped": cancel.is_set(),
    }
    benchmark.save_scope_benchmark(run)
    return run


def publish_scope_progress(loop: asyncio.AbstractEventLoop, hub: Hub, rule_id: str, event: dict) -> None:
    """A step of the scope job onto the topic under the rule's id, kept for the rule and for the page."""
    shown = {"rule": rule_id, **event}
    benchmark_events.setdefault(rule_id, []).append(shown)
    if rule_id != SCOPE_JOB:   # the run's own notes are under the scope id already: once, not twice
        benchmark_events.setdefault(SCOPE_JOB, []).append(shown)
    loop.call_soon_threadsafe(hub.publish, BENCHMARK_TOPIC, shown)


async def scope_job(query: GuardBenchmarkScopeParams, hub: Hub) -> None:
    report = functools.partial(publish_scope_progress, asyncio.get_running_loop(), hub)
    cancel = benchmark_cancels[SCOPE_JOB]
    try:
        run = await asyncio.to_thread(guarded, run_scope_job, query, report, cancel)
        hub.publish(BENCHMARK_TOPIC, {"rule": SCOPE_JOB, "done": True, "run": run})
        hub.publish(GUARDS_TOPIC, await asyncio.to_thread(guarded, read_guards))
    except MethodError as failure:
        hub.publish(BENCHMARK_TOPIC, {"rule": SCOPE_JOB, "error": failure.message})
    except Exception as failure:  # noqa: BLE001 - the job must end on the topic whatever broke (the page waits on it)
        hub.publish(BENCHMARK_TOPIC, {"rule": SCOPE_JOB, "error": f"{type(failure).__name__}: {failure}"})
    finally:
        benchmark_jobs.pop(SCOPE_JOB, None)
        benchmark_cancels.pop(SCOPE_JOB, None)


async def guards_benchmark_scope(params: dict, hub: Hub) -> dict:
    query = validated(GuardBenchmarkScopeParams, params)
    if SCOPE_JOB in benchmark_jobs:
        return {"rule": SCOPE_JOB, "started": False, "running": True}
    benchmark_cancels[SCOPE_JOB] = threading.Event()
    benchmark_events[SCOPE_JOB] = []
    benchmark_jobs[SCOPE_JOB] = asyncio.create_task(scope_job(query, hub))
    return {"rule": SCOPE_JOB, "started": True, "running": True}


async def guards_benchmark_runs(params: dict, hub: Hub) -> list[dict]:
    """The saved scope runs, newest first."""
    return await asyncio.to_thread(benchmark.list_scope_benchmarks)


async def guards_benchmark_flush(params: dict, hub: Hub) -> dict:
    """Delete the saved scope runs (the rules keep their own verdicts)."""
    return {"flushed": await asyncio.to_thread(benchmark.flush_scope_benchmarks)}


async def guards_benchmark_flush_scenarios(params: dict, hub: Hub) -> dict:
    """Forget every kept scenario set (the CLI's --flush-tests): the next run generates them again."""
    return {"flushed": await asyncio.to_thread(benchmark.flush_scenarios)}


async def guards_benchmark_state(params: dict, hub: Hub) -> dict:
    """A rule's latest run as the page needs it after a reload: whether it is still running and every event
    so far (the topic carries the rest)."""
    query = validated(GuardBenchmarkStopParams, params)
    return {"rule": query.id, "running": query.id in benchmark_jobs, "events": list(benchmark_events.get(query.id, []))}


class GuardBenchmarkStopParams(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)


async def guards_benchmark_stop(params: dict, hub: Hub) -> dict:
    """Stop a rule's running benchmark at its next arm (the headless answer in flight comes back first);
    what ran so far is kept as a stopped verdict."""
    query = validated(GuardBenchmarkStopParams, params)
    cancel = benchmark_cancels.get(query.id)
    if cancel is None:
        return {"rule": query.id, "stopped": False, "running": False}
    cancel.set()
    return {"rule": query.id, "stopped": True, "running": True}


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
    "guards.benchmark": guards_benchmark,
    "guards.benchmark.stop": guards_benchmark_stop,
    "guards.benchmark.state": guards_benchmark_state,
    "guards.benchmark.running": guards_benchmark_running,
    "guards.benchmark.scope": guards_benchmark_scope,
    "guards.benchmark.runs": guards_benchmark_runs,
    "guards.benchmark.flush": guards_benchmark_flush,
    "guards.benchmark.flush_scenarios": guards_benchmark_flush_scenarios,
    "profile.get": profile_get,
    "server.restart": server_restart,
}
