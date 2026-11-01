"""Session JSONL parser for the browser UI.

Parses Claude Code session transcripts into structured conversation turns,
preserving all content types (text, tool_use, tool_result, thinking).
"""

import json
import time
from datetime import datetime
from pathlib import Path

from synapptic.sessions.pricing import DEFAULT_PRICING, price_for
from synapptic.scrub import scrub_text

# In-memory cache for session list (TTL 30 seconds)
session_cache = {"data": None, "timestamp": 0}
SESSION_CACHE_TTL = 30


def parse_session_full(jsonl_path: Path, scrub: bool = True) -> list[dict]:
    """Parse a session JSONL into full conversation turns for the browser.

    Uses string pre-filter to skip non-conversation lines (progress, file-history,
    system records) without JSON parsing — same compression trick as filter.py.
    Only lines containing "user" or "assistant" type markers get parsed.
    """
    raw_turns = []

    with open(jsonl_path) as f:
        for line in f:
            # Include compact_boundary system records alongside user/assistant
            is_conversation = '"type":"user"' in line or '"type":"assistant"' in line or '"type": "user"' in line or '"type": "assistant"' in line
            is_compaction = '"compact_boundary"' in line
            if not is_conversation and not is_compaction:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue

            record_type = record.get("type")

            # Compaction boundary → insert a marker turn
            if record_type == "system" and record.get("subtype") == "compact_boundary":
                meta = record.get("compactMetadata", {})
                pre_tokens = meta.get("preTokens", 0)
                trigger = meta.get("trigger", "auto")
                raw_turns.append({
                    "role": "system",
                    "uuid": record.get("uuid", ""),
                    "timestamp": record.get("timestamp", ""),
                    "blocks": [{"type": "compaction", "trigger": trigger, "pre_tokens": pre_tokens}],
                    "usage": None,
                    "model": None,
                })
                continue

            if record_type not in ("user", "assistant"):
                continue

            turn = parse_record(record, scrub=scrub)
            if turn and turn.get("blocks"):
                raw_turns.append(turn)

    # Merge consecutive same-role turns and attach tool_results to assistant turns
    return merge_turns(raw_turns)


def parse_record(record: dict, scrub: bool = True) -> dict | None:
    """Parse a single JSONL record into a structured turn."""
    record_type = record.get("type")
    message = record.get("message", {})
    content = message.get("content", "")
    timestamp = record.get("timestamp", "")

    blocks = []

    if record_type == "user":
        if isinstance(content, str):
            text = content.strip()
            if text:
                blocks.append({"type": "text", "text": scrub_text(text) if scrub else text})
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        blocks.append({"type": "text", "text": scrub_text(text) if scrub else text})
                elif btype == "tool_result":
                    result_content = block.get("content", "")
                    if isinstance(result_content, list):
                        parts = []
                        for part in result_content:
                            if isinstance(part, dict) and part.get("type") == "text":
                                parts.append(part.get("text", ""))
                        result_content = "\n".join(parts)
                    result_str = str(result_content)[:2000]
                    if scrub:
                        result_str = scrub_text(result_str)
                    blocks.append({
                        "type": "tool_result",
                        "tool_use_id": block.get("tool_use_id", ""),
                        "content": result_str,
                    })

    elif record_type == "assistant":
        if isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text = block.get("text", "").strip()
                    if text:
                        blocks.append({"type": "text", "text": scrub_text(text) if scrub else text})
                elif btype == "tool_use":
                    input_data = block.get("input", {})
                    if isinstance(input_data, dict):
                        summary = ", ".join(f"{k}: {str(v)[:100]}" for k, v in input_data.items())
                    else:
                        summary = str(input_data)[:200]
                    if scrub:
                        summary = scrub_text(summary)
                    blocks.append({
                        "type": "tool_use",
                        "id": block.get("id", ""),
                        "name": block.get("name", "unknown"),
                        "input_summary": summary,
                    })
                elif btype == "thinking":
                    text = block.get("thinking", "").strip()
                    if text:
                        blocks.append({
                            "type": "thinking",
                            "text": scrub_text(text[:5000]) if scrub else text[:5000],
                        })

    if not blocks:
        return None

    # Extract usage from assistant messages. Claude Code writes one API response as several records (one per
    # block), each with the response's full usage: "responses" keys it by message id so merging counts it once.
    usage = None
    model = None
    responses = {}
    if record_type == "assistant":
        raw_usage = message.get("usage", {})
        model = message.get("model")
        if raw_usage:
            usage = {
                "input_tokens": raw_usage.get("input_tokens", 0) or 0,
                "output_tokens": raw_usage.get("output_tokens", 0) or 0,
                "cache_read": raw_usage.get("cache_read_input_tokens", 0) or 0,
                "cache_create": raw_usage.get("cache_creation_input_tokens", 0) or 0,
                # the part of cache_create written with the 1-hour duration (priced 2x input, not 1.25x)
                "cache_create_1h": (raw_usage.get("cache_creation") or {}).get("ephemeral_1h_input_tokens", 0) or 0,
            }
            responses = {message.get("id") or record.get("uuid", ""): {"model": model, "usage": usage}}

    return {
        "role": record_type,
        "uuid": record.get("uuid", ""),
        "timestamp": timestamp,
        "blocks": blocks,
        "usage": usage,
        "model": model,
        "responses": responses,
    }


USAGE_KEYS = ("input_tokens", "output_tokens", "cache_read", "cache_create", "cache_create_1h")


def summed_usage(responses: dict) -> dict | None:
    """One turn's usage: the sum over its distinct API responses."""
    if not responses:
        return None
    return {key: sum(response["usage"].get(key, 0) for response in responses.values()) for key in USAGE_KEYS}


def merge_turns(raw_turns: list[dict]) -> list[dict]:
    """Merge consecutive same-role turns into single conversation exchanges.

    Rules:
    - Consecutive assistant records → merge blocks, sum usage, keep first timestamp
    - User records that contain ONLY tool_result blocks → attach to preceding assistant turn
    - User records with real text → start a new user turn
    """
    if not raw_turns:
        return []

    merged = []
    for turn in raw_turns:
        # System turns (compaction markers) are never merged
        if turn["role"] == "system":
            merged.append(turn)
            continue

        # User turn with only tool_result blocks → attach to previous assistant
        if turn["role"] == "user":
            has_text = any(b["type"] == "text" for b in turn["blocks"])
            has_tool_result = any(b["type"] == "tool_result" for b in turn["blocks"])
            if not has_text and has_tool_result and merged and merged[-1]["role"] == "assistant":
                merged[-1]["blocks"].extend(turn["blocks"])
                continue

        # Same role as previous → merge
        if merged and merged[-1]["role"] == turn["role"]:
            prev = merged[-1]
            prev["blocks"].extend(turn["blocks"])
            # Sum usage for assistant turns, each API response once (its records repeat the same usage)
            if turn["role"] == "assistant" and turn.get("responses"):
                prev_responses = prev.setdefault("responses", {})
                for response_id, response in turn["responses"].items():
                    prev_responses.setdefault(response_id, response)
                prev["usage"] = summed_usage(prev_responses)
            if not prev["model"] and turn["model"]:
                prev["model"] = turn["model"]
            continue

        merged.append(turn)

    return merged


def response_cost(usage: dict, rates: dict) -> float:
    """USD for one API response. Rates per million tokens; cache rates missing from a table fall back to the
    standard multiples of input (reads 0.1x, 5-minute writes 1.25x, 1-hour writes 2x). A "long_prompt" tier
    (Haiku 5.5) applies when the prompt (input + cache read + cache write) is over its threshold."""
    inp = usage.get("input_tokens", 0) or 0
    out = usage.get("output_tokens", 0) or 0
    read = usage.get("cache_read", 0) or 0
    write = usage.get("cache_create", 0) or 0
    write_1h = min(usage.get("cache_create_1h", 0) or 0, write)
    tier = rates.get("long_prompt")
    if tier and inp + read + write > tier["over"]:
        rates = tier
    base = rates["input"]
    return (inp * base + out * rates["output"] + read * rates.get("cache_read", base * 0.1)
            + (write - write_1h) * rates.get("cache_write_5m", base * 1.25)
            + write_1h * rates.get("cache_write_1h", base * 2)) / 1_000_000


SHOWN_BLOCK_TYPES = frozenset({"text", "tool_use", "compaction"})


def count_conversation_turns(turns: list[dict]) -> int:
    """Turns the conversation shows: merged turns with text, a tool call or a compaction marker (thinking and
    tool output alone show nothing). The dashboard's sessions page displays exactly these."""
    return sum(1 for turn in turns if any(block["type"] in SHOWN_BLOCK_TYPES for block in turn["blocks"]))


def compute_session_stats(turns: list[dict], model_pricing: dict | None = None, default_pricing: dict | None = None) -> dict:
    """Aggregate token usage across all turns in a session.

    Returns a dict compatible with the metrics bar fields:
      total_requests, total_input_tokens, total_output_tokens,
      total_cache_read_tokens, total_cache_create_tokens, estimated_cost_usd
    """
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_create = 0
    requests = 0
    cost = 0.0

    for turn in turns:
        if turn.get("role") != "assistant":
            continue
        # one entry per API response; a turn built without "responses" counts as one response
        responses = turn.get("responses") or (
            {"turn": {"model": turn.get("model"), "usage": turn["usage"]}} if turn.get("usage") else {})
        for response in responses.values():
            usage = response["usage"]
            total_input += usage.get("input_tokens", 0) or 0
            total_output += usage.get("output_tokens", 0) or 0
            total_cache_read += usage.get("cache_read", 0) or 0
            total_cache_create += usage.get("cache_create", 0) or 0
            requests += 1
            model = response.get("model") or turn.get("model") or ""
            rates = price_for(model) if model_pricing is None else model_pricing.get(model, default_pricing or DEFAULT_PRICING)
            cost += response_cost(usage, rates)

    return {
        "total_requests": requests,
        "total_input_tokens": total_input,
        "total_output_tokens": total_output,
        "total_cache_read_tokens": total_cache_read,
        "total_cache_create_tokens": total_cache_create,
        "estimated_cost_usd": round(cost, 4),
    }


def list_all_sessions() -> list[dict]:
    """List all Claude Code sessions across all projects.

    Fast: uses file stat for size/date, reads only the first 50 lines
    to extract model + first timestamp. Message count estimated from file size.
    Cached in memory for 30 seconds.
    """
    now = time.time()
    if session_cache["data"] is not None and now - session_cache["timestamp"] < SESSION_CACHE_TTL:
        return session_cache["data"]
    from synapptic.config import CLAUDE_PROJECTS_DIR
    from synapptic.state import include_headless_sessions, is_excluded_transcript, load_config, slug_from_project_dir

    if not CLAUDE_PROJECTS_DIR.exists():
        return []

    include_headless = include_headless_sessions(load_config())
    sessions = []
    for project_dir in CLAUDE_PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue
        slug = slug_from_project_dir(project_dir.name)
        for jsonl_file in project_dir.glob("*.jsonl"):
            session_id = jsonl_file.stem
            stat = jsonl_file.stat()
            if stat.st_size < 100:
                continue
            if is_excluded_transcript(jsonl_file, include_headless):
                continue

            # Quick scan: only read first 50 lines to find model + first timestamp
            model = None
            first_ts = None
            try:
                with open(jsonl_file) as f:
                    for i, line in enumerate(f):
                        if i >= 50:
                            break
                        if '"type":"assistant"' not in line and '"type": "assistant"' not in line and '"type":"user"' not in line and '"type": "user"' not in line:
                            continue
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        rtype = record.get("type")
                        if rtype in ("user", "assistant"):
                            if not first_ts:
                                first_ts = record.get("timestamp", "")
                            if rtype == "assistant" and not model:
                                model = record.get("message", {}).get("model")
                        if model and first_ts:
                            break
            except Exception:
                continue

            # Estimate message count from file size (avg ~2KB per conversation line)
            est_messages = max(1, stat.st_size // 2048)

            sessions.append({
                "id": session_id,
                "project": slug,
                "project_dir": project_dir.name,
                "model": model or "unknown",
                "messages": est_messages,
                "size_kb": stat.st_size // 1024,
                "first_timestamp": first_ts or "",
                "last_timestamp": "",
                "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })

    sessions.sort(key=lambda s: s["modified"], reverse=True)
    session_cache["data"] = sessions
    session_cache["timestamp"] = time.time()
    return sessions


def list_projects_with_sessions() -> list[dict]:
    """Group sessions by project for the sidebar."""
    all_sessions = list_all_sessions()
    projects = {}
    for session in all_sessions:
        slug = session["project"]
        if slug not in projects:
            projects[slug] = {"slug": slug, "sessions": 0, "latest": ""}
        projects[slug]["sessions"] += 1
        if session["modified"] > projects[slug]["latest"]:
            projects[slug]["latest"] = session["modified"]

    result = sorted(projects.values(), key=lambda p: p["latest"], reverse=True)
    return result
