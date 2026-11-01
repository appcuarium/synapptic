"""Live-session pipeline: extract a session in chunks from a saved byte offset, then rebuild the profiles.

A checkpoint reads the transcript from where the previous one stopped, so a session that stays open for weeks
is learned from as it goes. Chunks end right after a user prompt, so a correction stays with the assistant
message it answers. Profiles are rebuilt from every observation file, so a chunk merged twice, or a hook
running alongside ingest, cannot count anything twice.
"""

from datetime import datetime, timezone
from pathlib import Path

from synapptic.config import CHARS_PER_TOKEN
from synapptic.extract import try_extract_observations
from synapptic.filter import conversation_turn, filter_transcript, iter_records
from synapptic.profile import profile_changed, promote_to_global, rebuild_profile, route_observations
from synapptic.state import (
    find_all_observations,
    list_projects,
    load_cursor,
    load_profile,
    save_cursor,
    save_observations,
    save_profile,
    whole_session_observation_file,
)


def rebuild_profiles() -> list[str | None]:
    """Recompute global and every project profile from all observation files; save the ones that changed.

    Returns the changed profiles (a project slug, or None for global).
    """
    global_obs, project_obs = [], {}
    for _, slug, obs_list in find_all_observations():
        global_part, project_part = route_observations(obs_list)
        global_obs.extend(global_part)
        if slug and project_part:
            project_obs.setdefault(slug, []).extend(project_part)

    changed = []
    rebuilt_projects = {}
    for slug in sorted(set(project_obs) | set(list_projects())):
        previous = load_profile(project_slug=slug)
        rebuilt_projects[slug] = rebuild_profile(project_obs.get(slug, []), previous)
        if profile_changed(previous, rebuilt_projects[slug]):
            save_profile(rebuilt_projects[slug], project_slug=slug)
            changed.append(slug)

    previous_global = load_profile(project_slug=None)
    promotions = promote_to_global(rebuilt_projects, rebuild_profile(global_obs, previous_global))
    rebuilt_global = rebuild_profile(global_obs, previous_global, promotions)
    if profile_changed(previous_global, rebuilt_global):
        save_profile(rebuilt_global, project_slug=None)
        changed.append(None)
    return changed


def record_time(record: dict) -> datetime | None:
    """A transcript record's timestamp as an aware datetime, or None."""
    stamp = record.get("timestamp")
    if not isinstance(stamp, str) or not stamp:
        return None
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return None


def start_offset(session_id: str, path: Path) -> int:
    """Where the next checkpoint starts: the saved cursor; for a session extracted whole before cursors
    existed, the first record written after that extraction; otherwise the beginning."""
    cursor = load_cursor(session_id)
    if cursor is not None:
        return cursor
    whole = whole_session_observation_file(session_id)
    if whole is None:
        return 0
    extracted_at = datetime.fromtimestamp(whole.stat().st_mtime, timezone.utc)
    previous_end = 0
    for offset, record in iter_records(path):
        when = record_time(record)
        if when is not None and when > extracted_at:
            return previous_end
        previous_end = offset
    return previous_end


def plan_chunks(path: Path, start: int, end: int, max_chars: int, min_chars: int,
                flush: bool) -> tuple[list[tuple[int, int]], int]:
    """Split [start, end) into chunks of about max_chars of conversation, each ending right after a user prompt.

    Returns (chunks, new cursor). Without flush, the text after the last user prompt waits, and so does a
    remainder shorter than min_chars. With flush everything up to `end` is taken.
    """
    chunks, chunk_start, chars = [], start, 0
    last_user_end, chars_at_user = None, 0
    for offset, record in iter_records(path, start, end):
        turn = conversation_turn(record)
        if not turn:
            continue
        chars += len(turn.text)
        if turn.role == "user":
            last_user_end, chars_at_user = offset, chars
            if chars >= max_chars:
                chunks.append((chunk_start, offset))
                chunk_start, chars, last_user_end = offset, 0, None
    if flush:
        if chars:
            chunks.append((chunk_start, end))
        return chunks, end
    if last_user_end is not None and chars_at_user >= min_chars:
        chunks.append((chunk_start, last_user_end))
        return chunks, last_user_end
    return chunks, chunk_start


def checkpoint_session(session_id: str, path: Path, project_slug: str, flush: bool, max_tokens: int,
                       min_tokens: int, llm_config: dict, profile: dict | None = None) -> dict:
    """Extract what the session added since its cursor, chunk by chunk, and move the cursor.

    A failed extraction stops the run and leaves the cursor at that chunk, so the next checkpoint retries it.
    Returns {"start", "cursor", "chunks", "observations", "failed"}.
    """
    start = start_offset(session_id, path)
    end = path.stat().st_size
    chunks, cursor = plan_chunks(path, start, end, max_tokens * CHARS_PER_TOKEN,
                                 min_tokens * CHARS_PER_TOKEN, flush)
    report = {"start": start, "cursor": start, "chunks": 0, "observations": 0, "failed": False}
    for chunk_start, chunk_end in chunks:
        turns = filter_transcript(path, max_tokens=max_tokens, start=chunk_start, end=chunk_end)
        observations = try_extract_observations(turns, session_id, profile=profile, config=llm_config)
        if observations is None:
            report["failed"] = True
            report["cursor"] = chunk_start
            save_cursor(session_id, chunk_start)
            return report
        for obs in observations:
            obs["project"] = project_slug
        save_observations(session_id, observations, project_slug=project_slug, chunk_start=chunk_start)
        report["chunks"] += 1
        report["observations"] += len(observations)
    report["cursor"] = cursor
    save_cursor(session_id, cursor)
    return report
