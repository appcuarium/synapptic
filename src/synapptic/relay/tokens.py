"""Extract token counts from API responses."""

import json

from synapptic.relay.config import CHARS_PER_TOKEN


def estimate_tokens(text: str) -> int:
    """Rough token estimate from character count."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def extract_anthropic_usage_from_response(body: dict) -> dict:
    """Extract usage from a non-streaming Anthropic response."""
    usage = body.get("usage", {})
    return {
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
        "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
    }


def extract_anthropic_usage_from_sse(chunks: list[bytes]) -> dict:
    """Parse accumulated SSE chunks to extract usage from Anthropic streaming response.

    Anthropic SSE format:
        event: message_start\ndata: {"type":"message_start","message":{"usage":{"input_tokens":...}}}\n\n
        event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":...}}\n\n
    """
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}

    raw = b"".join(chunks).decode("utf-8", errors="replace")

    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            continue
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, ValueError):
            continue

        event_type = data.get("type", "")

        if event_type == "message_start":
            msg_usage = data.get("message", {}).get("usage", {})
            usage["input_tokens"] = msg_usage.get("input_tokens", 0)
            usage["cache_read_tokens"] = msg_usage.get("cache_read_input_tokens", 0)
            usage["cache_creation_tokens"] = msg_usage.get("cache_creation_input_tokens", 0)

        elif event_type == "message_delta":
            delta_usage = data.get("usage", {})
            usage["output_tokens"] = delta_usage.get("output_tokens", 0)

    return usage


def extract_openai_usage_from_response(body: dict) -> dict:
    """Extract usage from a non-streaming OpenAI response."""
    usage = body.get("usage", {})
    return {
        "input_tokens": usage.get("prompt_tokens", 0),
        "output_tokens": usage.get("completion_tokens", 0),
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }


def extract_openai_usage_from_sse(chunks: list[bytes]) -> dict:
    """Parse accumulated SSE chunks to extract usage from OpenAI streaming response.

    OpenAI includes usage in the final chunk when stream_options.include_usage=true.
    """
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0}

    raw = b"".join(chunks).decode("utf-8", errors="replace")

    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            continue
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, ValueError):
            continue

        if "usage" in data and data["usage"]:
            u = data["usage"]
            usage["input_tokens"] = u.get("prompt_tokens", 0)
            usage["output_tokens"] = u.get("completion_tokens", 0)

    return usage


def extract_anthropic_response_text_from_sse(chunks: list[bytes]) -> str:
    """Extract the assistant's text content from accumulated Anthropic SSE chunks."""
    parts = []
    raw = b"".join(chunks).decode("utf-8", errors="replace")
    for line in raw.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
        if data_str == "[DONE]":
            continue
        try:
            data = json.loads(data_str)
        except (json.JSONDecodeError, ValueError):
            continue
        if data.get("type") == "content_block_delta":
            delta = data.get("delta", {})
            if delta.get("type") == "text_delta":
                parts.append(delta.get("text", ""))
    return "".join(parts)


def summarize_messages(messages: list, provider: str) -> str:
    """Create a JSON representation of the messages array for storage.

    Preserves full text content. Tool calls shown as structured summaries.
    """
    summary = []
    for msg in messages:
        role = msg.get("role", "?")
        content = msg.get("content", "")

        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, str):
                    parts.append(block)
                elif isinstance(block, dict):
                    btype = block.get("type", "")
                    if btype == "text":
                        parts.append(block.get("text", ""))
                    elif btype == "tool_use":
                        parts.append(f"[tool_use: {block.get('name', '?')}({json.dumps(block.get('input', {}), ensure_ascii=False)})]")
                    elif btype == "tool_result":
                        tool_content = block.get("content", "")
                        if isinstance(tool_content, list):
                            tool_text = " ".join(b.get("text", "") for b in tool_content if isinstance(b, dict))
                        else:
                            tool_text = str(tool_content)
                        parts.append(f"[tool_result]\n{tool_text}")
                    elif btype == "thinking":
                        parts.append(f"[thinking]\n{block.get('thinking', '')}")
                    else:
                        parts.append(f"[{btype}]")
            text = "\n\n".join(parts)
        else:
            text = str(content)

        summary.append({"role": role, "content": text})
    return json.dumps(summary, ensure_ascii=False)


def extract_request_metadata(body: dict, provider: str) -> dict:
    """Extract pre-request metadata from the request body."""
    if provider == "anthropic":
        messages = body.get("messages", [])
        system = body.get("system", "")
        system_text = system if isinstance(system, str) else json.dumps(system)
        tools = body.get("tools", [])
        return {
            "model": body.get("model", "unknown"),
            "message_count": len(messages),
            "system_prompt_tokens": estimate_tokens(system_text),
            "tool_definitions_count": len(tools),
            "stream": body.get("stream", False),
        }
    else:
        messages = body.get("messages", [])
        tools = body.get("tools", [])
        system_msgs = [m for m in messages if m.get("role") == "system"]
        system_text = " ".join(m.get("content", "") for m in system_msgs)
        return {
            "model": body.get("model", "unknown"),
            "message_count": len(messages),
            "system_prompt_tokens": estimate_tokens(system_text),
            "tool_definitions_count": len(tools),
            "stream": body.get("stream", False),
        }
