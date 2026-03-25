"""Request logging — ties together session tracking, token counting, and SQLite persistence."""

import time
from dataclasses import dataclass, field


@dataclass
class RequestContext:
    """Accumulates metadata for a single request lifecycle."""

    session_id: str = ""
    provider: str = ""
    model: str = "unknown"
    message_count: int = 0
    system_prompt_tokens: int = 0
    tool_definitions_count: int = 0
    stream: bool = False
    start_time: float = field(default_factory=time.monotonic)

    # Populated after response
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0

    # Message bodies (JSON strings)
    request_messages: str = ""
    response_content: str = ""

    @property
    def latency_ms(self) -> float:
        return (time.monotonic() - self.start_time) * 1000

    def to_store_kwargs(self) -> dict:
        """Convert to kwargs for MetricsStore.insert_request."""
        return {
            "session_id": self.session_id,
            "provider": self.provider,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_creation_tokens": self.cache_creation_tokens,
            "message_count": self.message_count,
            "system_prompt_tokens": self.system_prompt_tokens,
            "tool_definitions_count": self.tool_definitions_count,
            "latency_ms": self.latency_ms,
            "stream": self.stream,
            "request_messages": self.request_messages,
            "response_content": self.response_content,
        }
