"""SQLite index of Claude Code sessions (~/.synapptic/sessions.db): one row per transcript, its search entry,
and the sessions the SessionEnd hook reported ended. Everything here is rebuilt by `synapptic index --full`."""

import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from synapptic.config import SYNAPPTIC_DIR

DB_PATH = SYNAPPTIC_DIR / "sessions.db"

SCHEMA = """
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
    indexed_at TEXT,
    turn_count INTEGER DEFAULT 0
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


class SessionStore:
    """Sync SQLite access to the session index; callers on an event loop wrap calls in asyncio.to_thread."""

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
        self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def index_session(self, session_id: str, project: str, project_dir: str,
                      title: str, model: str, first_ts: str, last_ts: str,
                      msg_count: int, file_size: int, file_path: str,
                      first_user_message: str = "", turn_count: int = 0):
        """Insert or update a session in the index."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """INSERT INTO session_index
               (session_id, project, project_dir, title, model, first_timestamp,
                last_timestamp, message_count, file_size, file_path, indexed_at, turn_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                title=?, model=?, last_timestamp=?, message_count=?, file_size=?, indexed_at=?, turn_count=?""",
            (session_id, project, project_dir, title, model, first_ts,
             last_ts, msg_count, file_size, file_path, now, turn_count,
             title, model, last_ts, msg_count, file_size, now, turn_count),
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
                "SELECT * FROM session_index WHERE project = ? ORDER BY last_timestamp DESC LIMIT ?",
                (project, limit),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM session_index ORDER BY last_timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_indexed_session(self, session_id: str) -> dict | None:
        """One session from the index, or None."""
        row = self._conn.execute("SELECT * FROM session_index WHERE session_id = ?", (session_id,)).fetchone()
        return dict(row) if row else None

    def get_indexed_projects(self) -> list[dict]:
        """Get projects from the index with session counts."""
        rows = self._conn.execute(
            """SELECT project, COUNT(*) as sessions, MAX(last_timestamp) as latest
               FROM session_index GROUP BY project ORDER BY latest DESC""",
        ).fetchall()
        return [dict(r) for r in rows]

    def get_project_dirs(self) -> dict[str, tuple[str, str]]:
        """Each project's Claude Code project directory name and its most recent transcript's path."""
        rows = self._conn.execute(
            """SELECT project, project_dir, file_path FROM session_index
               WHERE last_timestamp IS NOT NULL ORDER BY last_timestamp ASC""",
        ).fetchall()
        return {row["project"]: (row["project_dir"], row["file_path"]) for row in rows}

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

    def remove_indexed_session(self, session_id: str):
        """Drop a session from the index and its search entry."""
        self._conn.execute("DELETE FROM session_index WHERE session_id = ?", (session_id,))
        self._conn.execute("DELETE FROM session_fts WHERE session_id = ?", (session_id,))
        self._conn.commit()

    def add_ended_session(self, session_id: str):
        """Record a session the SessionEnd hook reported closed, so it no longer shows as live."""
        self._conn.execute(
            "INSERT OR REPLACE INTO ended_sessions (session_id, ended_at) VALUES (?, ?)",
            (session_id, time.time()),
        )
        self._conn.commit()

    def get_ended_session_ids(self) -> set:
        """Load all previously ended session IDs from the DB."""
        rows = self._conn.execute("SELECT session_id FROM ended_sessions").fetchall()
        return {r[0] for r in rows}
