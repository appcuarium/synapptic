"""A transcript read incrementally: each update() reads only what was appended since the last one.

A live Claude Code session keeps appending to its JSONL transcript. Reading the whole file again for every
refresh costs about 0.6 s for 80 MB and 11 s for 1.7 GB; reading only the new bytes costs milliseconds. The
reader keeps the merged conversation turns (sessions.parse rules) and the index metadata (record count,
timestamps, titles, model) up to date, so the dashboard's counts, its conversation view and `synapptic index`
all read a transcript the same way, in one pass.
"""

import json
import re
import threading
from pathlib import Path

from synapptic.sessions.parse import merge_turn, turn_from_record

# Lines worth decoding: conversation records, compaction markers, mid-turn messages and the two title records.
# One compiled search on the raw bytes: most lines (progress, file history, tool noise) are skipped undecoded.
DECODE_PATTERN = re.compile(rb'"type": ?"(?:user|assistant|custom-title|ai-title)"|"compact_boundary"|"queued_command"')
TITLE_LENGTH = 120
FIRST_MESSAGE_LENGTH = 500


def first_text(content) -> str:
    """The text of a user record's content: the string, or its first text block."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")
    return ""


class TranscriptTail:
    """One transcript, read up to `offset`. Call update() under `lock` when other threads read the same tail."""

    def __init__(self, path: Path, scrub: bool = True):
        self.path = Path(path)
        self.scrub = scrub
        self.lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.inode: int | None = None
        self.offset = 0
        self.pending = b""                 # the last line, until its newline is written
        self.turns: list[dict] = []
        self.message_count = 0             # user + assistant records (the index's message_count)
        self.first_timestamp = ""
        self.last_timestamp = ""
        self.model: str | None = None
        self.first_user_message = ""
        self.custom_title = ""             # the name the user gave the session
        self.ai_title = ""                 # Claude Code's generated title

    @property
    def title(self) -> str:
        """The user's name for the session, else Claude Code's title, else the start of the first message."""
        return self.custom_title or self.ai_title or self.first_user_message[:TITLE_LENGTH].split("\n")[0]

    def update(self) -> int | None:
        """Read what was appended. Returns the index of the first turn that changed or was added, None if no
        turn changed. A replaced or truncated file is read again from the start (returns 0)."""
        stat = self.path.stat()
        restarted = stat.st_ino != self.inode or stat.st_size < self.offset
        if restarted:
            self.reset()
            self.inode = stat.st_ino
        if stat.st_size == self.offset:
            return 0 if restarted and self.turns else None
        with open(self.path, "rb") as f:
            f.seek(self.offset)
            chunk = f.read(stat.st_size - self.offset)
        self.offset += len(chunk)
        lines = (self.pending + chunk).split(b"\n")
        self.pending = lines.pop()
        first_changed = None
        for raw in lines:
            if not DECODE_PATTERN.search(raw):
                continue
            changed = self.read_line(raw.decode("utf-8", errors="replace"))
            if changed is not None and (first_changed is None or changed < first_changed):
                first_changed = changed
        return 0 if restarted and self.turns else first_changed

    def read_line(self, line: str) -> int | None:
        """Account one complete line (one DECODE_PATTERN matched); the index of the turn it changed, if any."""
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            return None
        if not isinstance(record, dict):
            return None
        self.read_metadata(record)
        turn = turn_from_record(record, scrub=self.scrub)
        return merge_turn(self.turns, turn) if turn else None

    def read_metadata(self, record: dict) -> None:
        record_type = record.get("type")
        # The latest of each kind wins: a session can be renamed and retitled as it goes.
        if record_type == "custom-title":
            self.custom_title = record.get("customTitle") or record.get("title") or self.custom_title
            return
        if record_type == "ai-title":
            self.ai_title = record.get("aiTitle") or self.ai_title
            return
        if record_type not in ("user", "assistant"):
            return
        self.message_count += 1
        timestamp = record.get("timestamp", "")
        if timestamp:
            self.first_timestamp = self.first_timestamp or timestamp
            self.last_timestamp = timestamp
        message = record.get("message") or {}
        if record_type == "user" and not self.first_user_message:
            self.first_user_message = first_text(message.get("content", ""))[:FIRST_MESSAGE_LENGTH]
        if record_type == "assistant" and not self.model:
            self.model = message.get("model")
