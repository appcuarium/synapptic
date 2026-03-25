"""Session indexer — scans Claude Code JSONL files and populates the SQLite index.

Called by:
- `synapptic index` for initial full scan
- Session-end hook for incremental indexing of a single session
"""

import json
import sys
from pathlib import Path

from synapptic.config import CLAUDE_PROJECTS_DIR
from synapptic.relay.config import DB_PATH
from synapptic.relay.store import MetricsStore
from synapptic.state import slug_from_project_dir


def index_all_sessions(incremental: bool = True, verbose: bool = False) -> int:
    """Scan all Claude Code sessions and index them.

    incremental: if True, skip sessions already in the index.
    Returns the number of newly indexed sessions.
    """
    if not CLAUDE_PROJECTS_DIR.exists():
        print("No Claude projects found.", file=sys.stderr)
        return 0

    store = MetricsStore(DB_PATH)
    store.init_db()

    existing_ids = store.get_indexed_session_ids() if incremental else set()
    indexed = 0

    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        slug = slug_from_project_dir(project_dir.name)

        for jsonl_file in project_dir.glob("*.jsonl"):
            session_id = jsonl_file.stem
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
            )
            indexed += 1
            if verbose and indexed % 100 == 0:
                print(f"  indexed {indexed} sessions...", file=sys.stderr)

    store.close()
    return indexed


def index_single_session(session_id: str, jsonl_path: Path) -> bool:
    """Index a single session (called by session-end hook)."""
    if not jsonl_path.exists():
        return False

    meta = extract_session_metadata(jsonl_path)
    if not meta:
        return False

    project_dir = jsonl_path.parent
    slug = slug_from_project_dir(project_dir.name)

    store = MetricsStore(DB_PATH)
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
    )
    store.close()
    return True


def extract_session_metadata(jsonl_path: Path) -> dict | None:
    """Extract title, model, timestamps, message count from a session JSONL.

    Reads the first 100 conversation lines for metadata, then counts the rest quickly.
    """
    model = None
    first_ts = None
    last_ts = None
    msg_count = 0
    first_user_message = ""
    title = ""

    try:
        with open(jsonl_path) as f:
            for line in f:
                # Fast pre-filter
                if '"type":"user"' not in line and '"type":"assistant"' not in line and '"type": "user"' not in line and '"type": "assistant"' not in line and '"type":"custom-title"' not in line and '"type": "custom-title"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rtype = record.get("type")

                if rtype == "custom-title":
                    title = record.get("title", "")
                    continue

                if rtype not in ("user", "assistant"):
                    continue

                msg_count += 1
                ts = record.get("timestamp", "")
                if ts and not first_ts:
                    first_ts = ts
                if ts:
                    last_ts = ts

                if rtype == "user" and not first_user_message:
                    content = record.get("message", {}).get("content", "")
                    if isinstance(content, str):
                        first_user_message = content[:500]
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                first_user_message = block.get("text", "")[:500]
                                break

                if rtype == "assistant" and not model:
                    model = record.get("message", {}).get("model")

    except Exception:
        return None

    if msg_count == 0:
        return None

    # Use first user message as title if no custom title
    if not title and first_user_message:
        title = first_user_message[:120].split("\n")[0]

    return {
        "title": title,
        "model": model or "unknown",
        "first_timestamp": first_ts or "",
        "last_timestamp": last_ts or "",
        "message_count": msg_count,
        "first_user_message": first_user_message,
    }
