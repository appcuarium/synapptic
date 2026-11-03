"""Transcript pre-filter: what of a session the model gets to see.

The selection and the budget rules live in the synapptic-core package; this module reads the JSONL, hands
records over, and gives the turns back as `Turn` objects.
"""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from synapptic.config import CHARS_PER_TOKEN, DEFAULT_MAX_TOKENS
from synapptic.core import require_core
from synapptic.scrub import scrub_text


@dataclass
class Turn:
    role: str  # "user" or "assistant"
    text: str
    timestamp: str = ""
    boosted: bool = False
    boost_reason: str = ""


def iter_records(jsonl_path: Path, start: int = 0, end: int | None = None):
    """Yield (offset just past the line, record) for each JSON line that starts in [start, end).

    Offsets are bytes, so a caller can resume exactly where it stopped. Unparseable lines (a record still
    being written) are skipped without being yielded.
    """
    with open(jsonl_path, "rb") as f:
        f.seek(start)
        offset = start
        for raw in f:
            if end is not None and offset >= end:
                break
            offset += len(raw)
            line = raw.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            yield offset, record


def conversation_turn(record: dict) -> Turn | None:
    """The user or assistant text a record carries, or None for every other record."""
    turn = require_core().conversation_turn(json.dumps(record))
    return Turn(**turn) if turn else None


def filter_transcript(jsonl_path: Path, max_tokens: int = DEFAULT_MAX_TOKENS,
                      start: int = 0, end: int | None = None) -> list[Turn]:
    """The conversation turns of a session's byte range [start, end), boosted and cut to the token budget."""
    return [Turn(**turn) for turn in require_core().filter_transcript(str(jsonl_path), max_tokens, start, end)]


def apply_boosts(turns: list[Turn]) -> None:
    """Mark, in place, the turns that carry correction, preference or reaction signals."""
    for turn, marked in zip(turns, require_core().apply_boosts([asdict(turn) for turn in turns])):
        turn.boosted = marked["boosted"]
        turn.boost_reason = marked["boost_reason"]


def truncate_to_budget(turns: list[Turn], max_chars: int) -> list[Turn]:
    """The turns that fit the character budget, the important ones first."""
    return [Turn(**turn) for turn in require_core().truncate_to_budget([asdict(turn) for turn in turns], max_chars)]


def turns_to_text(turns: list[Turn], scrub: bool = True) -> str:
    """The turns as the text document the model reads, secrets scrubbed unless scrub=False (debugging)."""
    parts = []
    for turn in turns:
        prefix = "USER" if turn.role == "user" else "ASSISTANT"
        text = scrub_text(turn.text) if scrub else turn.text
        parts.append(f"[{prefix}]: {text}")
    return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough token count estimate."""
    return len(text) // CHARS_PER_TOKEN
