"""A transcript read incrementally: each update() reads only what was appended since the last one.

A live Claude Code session keeps appending to its JSONL transcript. Reading the whole file again for every
refresh costs about 0.6 s for 80 MB and 11 s for 1.7 GB; reading only the new bytes costs milliseconds. The
reader keeps the merged conversation turns (sessions.parse rules) and the index metadata (record count,
timestamps, titles, model) up to date, so the dashboard's counts, its conversation view and `synapptic index`
all read a transcript the same way, in one pass.
"""

import hashlib
import json
import os
import re
import threading
from pathlib import Path

from synapptic.config import SYNAPPTIC_DIR
from synapptic.sessions.parse import merge_turn, turn_from_record

# Lines worth decoding: conversation records, compaction markers, mid-turn messages and the two title records.
# One compiled search on the raw bytes: most lines (progress, file history, tool noise) are skipped undecoded.
DECODE_PATTERN = re.compile(rb'"type": ?"(?:user|assistant|custom-title|ai-title)"|"compact_boundary"|"queued_command"')
TITLE_LENGTH = 120
FIRST_MESSAGE_LENGTH = 500
# What a reader parsed is kept on disk, so the next process (a dashboard restart, `synapptic index`) resumes
# from the offset read instead of parsing the whole transcript again: 3.4 s for 1.7 GB, 0.2 s to load back.
# Saved the first time and then once a megabyte more has been read; a few new lines are cheap to read again.
TAIL_CACHE_DIR = SYNAPPTIC_DIR / "cache" / "tails"
TAIL_CACHE_VERSION = 1
TAIL_SAVE_MIN_BYTES = 1_000_000
SAVED_FIELDS = ("inode", "offset", "message_count", "first_timestamp", "last_timestamp", "model",
                "first_user_message", "custom_title", "ai_title")


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
        self.saved_offset = 0              # the offset the cache on disk holds (0: none)
        self.load()

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
    def cache_path(self) -> Path:
        name = hashlib.sha1(str(self.path.resolve()).encode("utf-8")).hexdigest()
        return TAIL_CACHE_DIR / f"{name}-{'scrubbed' if self.scrub else 'raw'}.json"

    def load(self) -> bool:
        """Resume from the cache on disk, when there is one for this file; update() checks it against the file."""
        try:
            saved = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        if not isinstance(saved, dict) or saved.get("version") != TAIL_CACHE_VERSION:
            return False
        for name in SAVED_FIELDS:
            setattr(self, name, saved.get(name))
        self.pending = bytes(saved.get("pending") or b"")
        self.turns = saved.get("turns") or []
        self.saved_offset = self.offset or 0
        return True

    def save(self) -> None:
        """Write what was read so far, atomically; the next reader of this file resumes from here."""
        document = {"version": TAIL_CACHE_VERSION, "pending": list(self.pending), "turns": self.turns}
        document.update({name: getattr(self, name) for name in SAVED_FIELDS})
        try:
            TAIL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
            temp = self.cache_path.with_suffix(f".tmp{os.getpid()}")
            temp.write_text(json.dumps(document), encoding="utf-8")
            temp.replace(self.cache_path)
            self.saved_offset = self.offset
        except OSError:
            return

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
            self.saved_offset = 0
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
        if not self.saved_offset or self.offset - self.saved_offset >= TAIL_SAVE_MIN_BYTES:
            self.save()
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
