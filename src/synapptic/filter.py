"""Transcript pre-filter — pure Python, no LLM.

Stream-parses session JSONL files, extracting only preference-relevant content.
Reduces 100MB+ transcripts to ~50K tokens of signal.
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from synapptic.config import (
    CHARS_PER_TOKEN,
    CORRECTION_SIGNALS,
    DEFAULT_MAX_TOKENS,
    PREFERENCE_SIGNALS,
    STRONG_REACTION_SIGNALS,
)


@dataclass
class Turn:
    role: str  # "user" or "assistant"
    text: str
    timestamp: str = ""
    boosted: bool = False
    boost_reason: str = ""


def filter_transcript(jsonl_path: Path, max_tokens: int = DEFAULT_MAX_TOKENS) -> list[Turn]:
    """Stream-parse a session JSONL, extract only preference-relevant content.

    Keeps:
    - User messages where content is a string (not tool_result arrays)
    - Assistant text blocks only (strips tool_use, thinking blocks)
    - Chronological pairs: [what Claude said] → [what user said next]

    Strips:
    - progress records (~40-50% of lines)
    - file-history-snapshot records
    - system records
    - queue-operation records
    - last-prompt records
    - tool_result content blocks (from user messages)
    - tool_use input blocks (the actual file contents, command outputs)
    - thinking blocks from assistant messages
    """
    turns = []
    max_chars = max_tokens * CHARS_PER_TOKEN

    with open(jsonl_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            record_type = record.get("type")

            # Skip non-conversation records
            if record_type in ("progress", "file-history-snapshot", "system",
                               "queue-operation", "last-prompt"):
                continue

            if record_type == "user":
                turn = extract_user_turn(record)
                if turn:
                    turns.append(turn)

            elif record_type == "assistant":
                turn = extract_assistant_turn(record)
                if turn:
                    turns.append(turn)

    # Apply heuristic boosting
    apply_boosts(turns)

    # Truncate to max_tokens if needed
    turns = truncate_to_budget(turns, max_chars)

    return turns


def extract_user_turn(record: dict) -> Turn | None:
    """Extract user text from a user record."""
    message = record.get("message", {})
    content = message.get("content", "")
    timestamp = record.get("timestamp", "")

    # String content = direct user message
    if isinstance(content, str):
        text = content.strip()
        if text:
            return Turn(role="user", text=text, timestamp=timestamp)

    # List content = could be tool_result or mixed
    elif isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, dict):
                # Only keep text blocks, skip tool_result
                if block.get("type") == "text":
                    t = block.get("text", "").strip()
                    if t:
                        text_parts.append(t)
            elif isinstance(block, str):
                text_parts.append(block)
        if text_parts:
            return Turn(role="user", text="\n".join(text_parts), timestamp=timestamp)

    return None


def extract_assistant_turn(record: dict) -> Turn | None:
    """Extract assistant text from an assistant record, stripping tool_use and thinking."""
    message = record.get("message", {})
    content = message.get("content", [])
    timestamp = record.get("timestamp", "")

    if isinstance(content, str):
        text = content.strip()
        if text:
            return Turn(role="assistant", text=text, timestamp=timestamp)

    elif isinstance(content, list):
        text_parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            # Only keep text blocks
            if block.get("type") == "text":
                t = block.get("text", "").strip()
                if t:
                    text_parts.append(t)
            # Skip tool_use, thinking, tool_result
        if text_parts:
            return Turn(role="assistant", text="\n".join(text_parts), timestamp=timestamp)

    return None


def apply_boosts(turns: list[Turn]):
    """Mark turns that contain correction/preference signals as boosted."""
    for i, turn in enumerate(turns):
        if turn.role != "user":
            continue

        text_lower = turn.text.lower()

        # Check correction signals
        for signal in CORRECTION_SIGNALS:
            if signal.lower() in text_lower:
                turn.boosted = True
                turn.boost_reason = f"correction: '{signal}'"
                break

        if turn.boosted:
            continue

        # Check preference signals
        for signal in PREFERENCE_SIGNALS:
            if signal.lower() in text_lower:
                turn.boosted = True
                turn.boost_reason = f"preference: '{signal}'"
                break

        if turn.boosted:
            continue

        # Check strong reactions
        for signal in STRONG_REACTION_SIGNALS:
            if signal in turn.text:  # case-sensitive for caps
                turn.boosted = True
                turn.boost_reason = f"reaction: '{signal}'"
                break

        if turn.boosted:
            continue

        # Short user message after long assistant message = likely correction
        if (i > 0 and turns[i - 1].role == "assistant"
                and len(turn.text) < 100
                and len(turns[i - 1].text) > 500):
            turn.boosted = True
            turn.boost_reason = "short reply after long response"


def truncate_to_budget(turns: list[Turn], max_chars: int) -> list[Turn]:
    """Truncate turns to fit within character budget.

    Strategy:
    1. If total fits, return all
    2. Otherwise, keep all boosted turns + their preceding assistant turn
    3. Fill remaining budget with non-boosted turns from the end (recent = more relevant)
    """
    total_chars = sum(len(t.text) for t in turns)
    if total_chars <= max_chars:
        return turns

    # Phase 1: collect boosted turns with context
    priority_indices = set()
    for i, turn in enumerate(turns):
        if turn.boosted:
            priority_indices.add(i)
            # Include preceding assistant turn for context
            if i > 0 and turns[i - 1].role == "assistant":
                priority_indices.add(i - 1)

    priority_turns = [(i, turns[i]) for i in sorted(priority_indices)]
    priority_chars = sum(len(t.text) for _, t in priority_turns)

    # If priority turns alone exceed budget, truncate each
    if priority_chars > max_chars:
        result = []
        budget = max_chars
        for _, turn in priority_turns:
            if budget <= 0:
                break
            if len(turn.text) > budget:
                turn.text = turn.text[:budget] + "..."
            budget -= len(turn.text)
            result.append(turn)
        return result

    # Phase 2: fill remaining budget with recent non-boosted turns
    remaining_budget = max_chars - priority_chars
    result_indices = set(priority_indices)
    filler = []

    for i in range(len(turns) - 1, -1, -1):
        if i in result_indices:
            continue
        if remaining_budget <= 0:
            break
        turn = turns[i]
        if len(turn.text) <= remaining_budget:
            filler.append(i)
            remaining_budget -= len(turn.text)

    result_indices.update(filler)

    return [turns[i] for i in sorted(result_indices)]


def turns_to_text(turns: list[Turn]) -> str:
    """Convert filtered turns into a text document for LLM consumption."""
    parts = []
    for turn in turns:
        prefix = "USER" if turn.role == "user" else "ASSISTANT"
        parts.append(f"[{prefix}]: {turn.text}")
    return "\n\n".join(parts)


def estimate_tokens(text: str) -> int:
    """Rough token count estimate."""
    return len(text) // CHARS_PER_TOKEN
