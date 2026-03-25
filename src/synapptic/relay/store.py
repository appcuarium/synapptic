"""SQLite persistence for proxy metrics."""

import sqlite3
import asyncio
from datetime import datetime, timezone
from pathlib import Path

from synapptic.relay.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    last_active TEXT NOT NULL,
    total_requests INTEGER DEFAULT 0,
    total_input_tokens INTEGER DEFAULT 0,
    total_output_tokens INTEGER DEFAULT 0,
    total_cache_read_tokens INTEGER DEFAULT 0,
    total_cache_creation_tokens INTEGER DEFAULT 0,
    client_fingerprint TEXT,
    session_key TEXT
);

CREATE TABLE IF NOT EXISTS request_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    cache_read_tokens INTEGER DEFAULT 0,
    cache_creation_tokens INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    system_prompt_tokens INTEGER DEFAULT 0,
    tool_definitions_count INTEGER DEFAULT 0,
    latency_ms REAL DEFAULT 0,
    stream BOOLEAN DEFAULT 0,
    request_messages TEXT,
    response_content TEXT,
    FOREIGN KEY (session_id) REFERENCES sessions(id)
);

CREATE INDEX IF NOT EXISTS idx_request_log_session ON request_log(session_id);
CREATE INDEX IF NOT EXISTS idx_request_log_timestamp ON request_log(timestamp);

CREATE TABLE IF NOT EXISTS session_index (
    session_id TEXT PRIMARY KEY,
    project TEXT NOT NULL,
    project_dir TEXT NOT NULL,
    title TEXT,
    model TEXT,
    first_timestamp TEXT,
    last_timestamp TEXT,
    message_count INTEGER DEFAULT 0,
    file_size INTEGER DEFAULT 0,
    file_path TEXT,
    indexed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_session_index_project ON session_index(project);
CREATE VIRTUAL TABLE IF NOT EXISTS session_fts USING fts5(
    session_id UNINDEXED, title, first_user_message
);

CREATE TABLE IF NOT EXISTS ended_sessions (
    session_id TEXT PRIMARY KEY,
    ended_at REAL NOT NULL
);
"""


class MetricsStore:
    """Thread-safe SQLite store for proxy metrics. All public methods are sync — wrap in asyncio.to_thread for async."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self._conn: sqlite3.Connection | None = None

    def init_db(self):
        """Create tables if they don't exist."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # WAL mode: readers and writer coexist freely; busy_timeout prevents "database is locked"
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self):
        """Add columns that may be missing from older DBs."""
        req_columns = {r[1] for r in self._conn.execute("PRAGMA table_info(request_log)").fetchall()}
        if "request_messages" not in req_columns:
            self._conn.execute("ALTER TABLE request_log ADD COLUMN request_messages TEXT")
        if "response_content" not in req_columns:
            self._conn.execute("ALTER TABLE request_log ADD COLUMN response_content TEXT")

        sess_columns = {r[1] for r in self._conn.execute("PRAGMA table_info(sessions)").fetchall()}
        if "session_key" not in sess_columns:
            self._conn.execute("ALTER TABLE sessions ADD COLUMN session_key TEXT")

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def upsert_session(self, session_id: str, fingerprint: str, session_key: str = ""):
        """Create session if not exists, update last_active."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO sessions (id, started_at, last_active, client_fingerprint, session_key)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET last_active = ?""",
            (session_id, now, now, fingerprint, session_key, now),
        )
        self._conn.commit()

    def insert_request(self, session_id: str, provider: str, model: str, input_tokens: int, output_tokens: int,
                       cache_read_tokens: int, cache_creation_tokens: int, message_count: int,
                       system_prompt_tokens: int, tool_definitions_count: int, latency_ms: float, stream: bool,
                       request_messages: str = "", response_content: str = ""):
        """Log a request and update session totals."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO request_log
               (session_id, timestamp, provider, model, input_tokens, output_tokens,
                cache_read_tokens, cache_creation_tokens, message_count,
                system_prompt_tokens, tool_definitions_count, latency_ms, stream,
                request_messages, response_content)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (session_id, now, provider, model, input_tokens, output_tokens,
             cache_read_tokens, cache_creation_tokens, message_count,
             system_prompt_tokens, tool_definitions_count, latency_ms, stream,
             request_messages, response_content),
        )
        self._conn.execute(
            """UPDATE sessions SET
               total_requests = total_requests + 1,
               total_input_tokens = total_input_tokens + ?,
               total_output_tokens = total_output_tokens + ?,
               total_cache_read_tokens = total_cache_read_tokens + ?,
               total_cache_creation_tokens = total_cache_creation_tokens + ?,
               last_active = ?
               WHERE id = ?""",
            (input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens, now, session_id),
        )
        self._conn.commit()

    def get_session(self, session_id: str) -> dict | None:
        """Get session by ID."""
        row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def get_recent_requests(self, session_id: str, limit: int = 50) -> list[dict]:
        """Get recent requests for a session."""
        rows = self._conn.execute(
            "SELECT * FROM request_log WHERE session_id = ? ORDER BY timestamp DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_request(self, request_id: int) -> dict | None:
        """Get a single request by ID, including bodies."""
        row = self._conn.execute("SELECT * FROM request_log WHERE id = ?", (request_id,)).fetchone()
        return dict(row) if row else None

    def get_session_turns(self, session_id: str) -> list[dict]:
        """Get conversation turns for a relay session, formatted for the browser UI.

        Parses request_messages JSON to extract the last user message,
        skipping system prompts and tool results.
        """
        import json as json_mod
        rows = self._conn.execute(
            "SELECT timestamp, model, input_tokens, output_tokens, cache_read_tokens, "
            "cache_creation_tokens, request_messages, response_content "
            "FROM request_log WHERE session_id = ? ORDER BY timestamp ASC",
            (session_id,),
        ).fetchall()
        turns = []
        seen_user_messages = set()
        prev_msg_count = 0

        for row in rows:
            r = dict(row)

            # Extract ALL user messages from this request, but only show NEW ones
            # (each request sends the full conversation history)
            user_messages = []
            if r.get("request_messages"):
                try:
                    messages = json_mod.loads(r["request_messages"])
                    if isinstance(messages, list):
                        for msg in messages:
                            if not isinstance(msg, dict) or msg.get("role") != "user":
                                continue
                            content = msg.get("content", "")
                            if isinstance(content, str):
                                text = content
                            elif isinstance(content, list):
                                parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
                                text = "\n".join(parts)
                            else:
                                continue
                            if text:
                                user_messages.append(text)
                except (json_mod.JSONDecodeError, TypeError):
                    user_messages = [r["request_messages"][:500]]

            # Only show user messages we haven't seen before
            for text in user_messages:
                msg_key = text[:200]
                if msg_key not in seen_user_messages:
                    seen_user_messages.add(msg_key)
                    turns.append({
                        "role": "user",
                        "uuid": "",
                        "timestamp": r["timestamp"],
                        "blocks": [{"type": "text", "text": text[:5000]}],
                        "usage": None,
                        "model": None,
                    })

            # Assistant response
            if r.get("response_content"):
                turns.append({
                    "role": "assistant",
                    "uuid": "",
                    "timestamp": r["timestamp"],
                    "blocks": [{"type": "text", "text": r["response_content"]}],
                    "usage": {
                        "input_tokens": r.get("input_tokens", 0),
                        "output_tokens": r.get("output_tokens", 0),
                        "cache_read": r.get("cache_read_tokens", 0),
                        "cache_create": r.get("cache_creation_tokens", 0),
                    },
                    "model": r.get("model"),
                })
        return turns

    def get_all_sessions(self, limit: int = 20) -> list[dict]:
        """Get recent sessions ordered by last activity."""
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY last_active DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_resumable_sessions(self, timeout_minutes: int = 30) -> list[dict]:
        """Get sessions active within the timeout window, for restoring tracker state on restart."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)).isoformat()
        rows = self._conn.execute(
            "SELECT id, session_key, last_active FROM sessions WHERE last_active > ? AND session_key IS NOT NULL",
            (cutoff,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_daily_totals(self) -> dict:
        """Get today's totals across all sessions."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        row = self._conn.execute(
            """SELECT
                COALESCE(SUM(input_tokens), 0) as input_tokens,
                COALESCE(SUM(output_tokens), 0) as output_tokens,
                COALESCE(SUM(cache_read_tokens), 0) as cache_read_tokens,
                COALESCE(SUM(cache_creation_tokens), 0) as cache_creation_tokens,
                COUNT(*) as request_count
               FROM request_log WHERE timestamp LIKE ?""",
            (f"{today}%",),
        ).fetchone()
        return dict(row)

    # --- Session index for browser ---

    def index_session(self, session_id: str, project: str, project_dir: str,
                      title: str, model: str, first_ts: str, last_ts: str,
                      msg_count: int, file_size: int, file_path: str,
                      first_user_message: str = ""):
        """Insert or update a session in the index."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO session_index
               (session_id, project, project_dir, title, model, first_timestamp,
                last_timestamp, message_count, file_size, file_path, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                title=?, model=?, last_timestamp=?, message_count=?, file_size=?, indexed_at=?""",
            (session_id, project, project_dir, title, model, first_ts,
             last_ts, msg_count, file_size, file_path, now,
             title, model, last_ts, msg_count, file_size, now),
        )
        # FTS index for search
        self._conn.execute("DELETE FROM session_fts WHERE session_id = ?", (session_id,))
        self._conn.execute(
            "INSERT INTO session_fts (session_id, title, first_user_message) VALUES (?, ?, ?)",
            (session_id, title or "", first_user_message[:2000]),
        )
        self._conn.commit()

    def get_indexed_sessions(self, project: str = None, limit: int = 200) -> list[dict]:
        """Get sessions from the index, sorted by most recently modified."""
        if project:
            rows = self._conn.execute(
                "SELECT * FROM session_index WHERE project = ? ORDER BY indexed_at DESC, first_timestamp DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM session_index ORDER BY indexed_at DESC, first_timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_indexed_projects(self) -> list[dict]:
        """Get projects from the index with session counts."""
        rows = self._conn.execute(
            """SELECT project, COUNT(*) as sessions, MAX(first_timestamp) as latest
               FROM session_index GROUP BY project ORDER BY latest DESC""",
        ).fetchall()
        return [dict(r) for r in rows]

    def search_sessions(self, query: str, limit: int = 50) -> list[dict]:
        """Full-text search across session titles and first messages."""
        # Add * for prefix matching so "bench" matches "benchmark"
        fts_query = " ".join(f'"{w}"*' for w in query.split() if w)
        if not fts_query:
            return []
        try:
            rows = self._conn.execute(
                """SELECT si.* FROM session_fts fts
                   JOIN session_index si ON fts.session_id = si.session_id
                   WHERE session_fts MATCH ? ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def get_index_count(self) -> int:
        """Number of indexed sessions."""
        row = self._conn.execute("SELECT COUNT(*) FROM session_index").fetchone()
        return row[0] if row else 0

    def get_indexed_session_ids(self) -> set:
        """Get all indexed session IDs for incremental indexing."""
        rows = self._conn.execute("SELECT session_id FROM session_index").fetchall()
        return {r[0] for r in rows}

    def add_ended_session(self, session_id: str):
        """Persist a session as ended so it survives relay restarts."""
        import time
        self._conn.execute(
            "INSERT OR REPLACE INTO ended_sessions (session_id, ended_at) VALUES (?, ?)",
            (session_id, time.time()),
        )
        self._conn.commit()

    def get_ended_session_ids(self) -> set:
        """Load all previously ended session IDs from the DB."""
        rows = self._conn.execute("SELECT session_id FROM ended_sessions").fetchall()
        return {r[0] for r in rows}

    async def async_insert_request(self, **kwargs):
        """Async wrapper for insert_request."""
        await asyncio.to_thread(self.insert_request, **kwargs)

    async def async_upsert_session(self, session_id: str, fingerprint: str, session_key: str = ""):
        """Async wrapper for upsert_session."""
        await asyncio.to_thread(self.upsert_session, session_id, fingerprint, session_key)
