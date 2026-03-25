"""SSE passthrough — yield chunks immediately to client, accumulate for post-processing."""

import asyncio
from collections.abc import AsyncIterator, Callable, Coroutine
from typing import Any

# Strong references to background tasks — prevents GC before completion (Python 3.12+)
_background_tasks: set[asyncio.Task] = set()


async def relay_sse_stream(
    response,
    on_complete: Callable[[list[bytes], dict], Coroutine[Any, Any, None]],
    response_headers: dict | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE chunks to client with zero delay. Fire callback after stream ends.

    Args:
        response: httpx async response (already entered as context manager).
        on_complete: async callback(accumulated_chunks, response_headers) fired after stream ends.
        response_headers: upstream response headers to pass to callback.
    """
    accumulated: list[bytes] = []

    async for chunk in response.aiter_bytes():
        accumulated.append(chunk)
        yield chunk

    # Stream complete — fire post-processing in background (don't block the client)
    task = asyncio.create_task(on_complete(accumulated, response_headers or {}))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
