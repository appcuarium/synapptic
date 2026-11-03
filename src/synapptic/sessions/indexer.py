"""Session indexer — scans Claude Code JSONL files and populates the SQLite index.

Called by:
- `synapptic index` for initial full scan
- Session-end hook for incremental indexing of a single session
"""

import sys
from pathlib import Path

from synapptic.config import CLAUDE_PROJECTS_DIR
from synapptic.providers import load_config
from synapptic.sessions.parse import count_conversation_turns
from synapptic.sessions.store import DB_PATH, SessionStore
from synapptic.sessions.tail import TranscriptTail
from synapptic.state import include_headless_sessions, is_excluded_transcript, slug_from_project_dir


def index_all_sessions(incremental: bool = True, verbose: bool = False) -> int:
    """Scan all Claude Code sessions and index them.

    incremental: if True, skip sessions already in the index.
    Transcripts that are not the user's sessions (state.is_excluded_transcript) are not indexed, and are
    removed if an earlier run indexed them.
    Returns the number of newly indexed sessions.
    """
    if not CLAUDE_PROJECTS_DIR.exists():
        print("No Claude projects found.", file=sys.stderr)
        return 0

    store = SessionStore(DB_PATH)
    store.init_db()

    include_headless = include_headless_sessions(load_config())
    indexed_ids = store.get_indexed_session_ids()
    existing_ids = indexed_ids if incremental else set()
    indexed = 0

    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        slug = slug_from_project_dir(project_dir.name)

        for jsonl_file in project_dir.glob("*.jsonl"):
            session_id = jsonl_file.stem
            if is_excluded_transcript(jsonl_file, include_headless):
                if session_id in indexed_ids:
                    store.remove_indexed_session(session_id)
                continue
            if session_id in existing_ids:
                continue

            meta = extract_session_metadata(jsonl_file)
            if not meta:
                continue

            store.index_session(
                session_id=session_id,
                project=slug,
                project_dir=project_dir.name,
                title=meta["title"],
                model=meta["model"],
                first_ts=meta["first_timestamp"],
                last_ts=meta["last_timestamp"],
                msg_count=meta["message_count"],
                file_size=jsonl_file.stat().st_size,
                file_path=str(jsonl_file),
                first_user_message=meta["first_user_message"],
                turn_count=meta["turn_count"],
            )
            indexed += 1
            if verbose and indexed % 100 == 0:
                print(f"  indexed {indexed} sessions...", file=sys.stderr)

    store.close()
    return indexed


def index_single_session(session_id: str, jsonl_path: Path) -> bool:
    """Index a single session (called by session-end hook). Not one of the user's sessions: not indexed."""
    if not jsonl_path.exists():
        return False
    if is_excluded_transcript(jsonl_path, include_headless_sessions(load_config())):
        return False

    meta = extract_session_metadata(jsonl_path)
    if not meta:
        return False

    project_dir = jsonl_path.parent
    slug = slug_from_project_dir(project_dir.name)

    store = SessionStore(DB_PATH)
    store.init_db()
    store.index_session(
        session_id=session_id,
        project=slug,
        project_dir=project_dir.name,
        title=meta["title"],
        model=meta["model"],
        first_ts=meta["first_timestamp"],
        last_ts=meta["last_timestamp"],
        msg_count=meta["message_count"],
        file_size=jsonl_path.stat().st_size,
        file_path=str(jsonl_path),
        first_user_message=meta["first_user_message"],
        turn_count=meta["turn_count"],
    )
    store.close()
    return True


def tail_metadata(tail: TranscriptTail) -> dict:
    """The index fields of a transcript read so far (the dashboard refreshes live sessions with this too)."""
    return {
        "title": tail.title,
        "model": tail.model or "unknown",
        "first_timestamp": tail.first_timestamp,
        "last_timestamp": tail.last_timestamp,
        "message_count": tail.message_count,
        "turn_count": count_conversation_turns(tail.turns),
        "first_user_message": tail.first_user_message,
    }


def extract_session_metadata(jsonl_path: Path) -> dict | None:
    """Title, model, timestamps, message and turn counts of a session JSONL, in one pass (sessions.tail).
    None if the file cannot be read or holds no user or assistant record."""
    tail = TranscriptTail(jsonl_path, scrub=False)   # only counted, never shown: no scrubbing
    try:
        tail.update()
    except (OSError, UnicodeError):
        return None
    if tail.message_count == 0:
        return None
    return tail_metadata(tail)
