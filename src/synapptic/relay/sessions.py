"""Session detection — tool identity + system prompt hash + timeout heuristic."""

import hashlib
import time
import uuid
from datetime import datetime, timezone

from synapptic.relay.config import SESSION_TIMEOUT_MINUTES


class _Session:
    __slots__ = ("id", "tool", "system_hash", "session_key", "last_active")

    def __init__(self, session_id: str, tool: str, system_hash: str, session_key: str):
        self.id = session_id
        self.tool = tool
        self.system_hash = system_hash
        self.session_key = session_key
        self.last_active = time.monotonic()

    def is_expired(self) -> bool:
        return (time.monotonic() - self.last_active) > (SESSION_TIMEOUT_MINUTES * 60)

    def touch(self):
        self.last_active = time.monotonic()


def _extract_tool(user_agent: str) -> str:
    """Extract tool name from User-Agent, ignoring version numbers."""
    ua = user_agent.lower()
    if "claude-code" in ua or "claude_code" in ua:
        return "claude-code"
    if "cursor" in ua:
        return "cursor"
    if "copilot" in ua or "go-gh" in ua:
        return "copilot"
    if "aider" in ua:
        return "aider"
    return ua.split("/")[0].strip() if "/" in ua else ua[:32]


class SessionTracker:
    """Track active sessions. Uses tool identity + system prompt hash to distinguish sessions.

    On init, loads recent sessions from SQLite so sessions survive proxy restarts.
    """

    def __init__(self):
        self._sessions: dict[str, _Session] = {}  # keyed by session_key

    def load_from_store(self, store):
        """Restore sessions from SQLite that are still within the timeout window."""
        try:
            resumable = store.get_resumable_sessions(SESSION_TIMEOUT_MINUTES)
        except Exception:
            return

        for row in resumable:
            session_key = row.get("session_key") or ""
            session_id = row["id"]
            if not session_key or session_key in self._sessions:
                continue

            # Parse tool and system_hash from session_key (format: "tool:hash" or "tool")
            parts = session_key.split(":", 1)
            tool = parts[0]
            system_hash = parts[1] if len(parts) > 1 else ""

            session = _Session(session_id, tool, system_hash, session_key)
            # Adjust last_active based on the stored timestamp so expiry is correct
            last_active_str = row.get("last_active", "")
            if last_active_str:
                try:
                    last_active_dt = datetime.fromisoformat(last_active_str)
                    age_seconds = (datetime.now(timezone.utc) - last_active_dt).total_seconds()
                    session.last_active = time.monotonic() - age_seconds
                except (ValueError, TypeError):
                    pass

            self._sessions[session_key] = session

    def get_or_create(self, source_ip: str, user_agent: str, system_hash: str = "") -> tuple[str, bool]:
        """Return (session_id, is_new) for this client/tool combo.

        Uses tool identity only (not system_hash) because Claude Code's system
        prompt grows with each turn, producing a different hash every call.
        is_new=True only on the first request of a fresh session.
        """
        tool = _extract_tool(user_agent)
        session_key = tool

        if session_key in self._sessions:
            session = self._sessions[session_key]
            if not session.is_expired():
                session.touch()
                return session.id, False
            else:
                del self._sessions[session_key]

        session_id = uuid.uuid4().hex[:12]
        self._sessions[session_key] = _Session(session_id, tool, system_hash, session_key)
        return session_id, True

    def get_session_key(self, source_ip: str, user_agent: str, system_hash: str = "") -> str:
        """Get the session key for a client."""
        tool = _extract_tool(user_agent)
        return tool

    def get_fingerprint(self, source_ip: str, user_agent: str) -> str:
        """Get tool-based fingerprint for a client."""
        tool = _extract_tool(user_agent)
        return hashlib.sha256(tool.encode()).hexdigest()[:16]

    @property
    def current_session_id(self) -> str | None:
        """Most recently active session, if any."""
        if not self._sessions:
            return None
        most_recent = max(self._sessions.values(), key=lambda s: s.last_active)
        return most_recent.id if not most_recent.is_expired() else None
