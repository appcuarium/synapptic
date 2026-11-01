"""Message Batches for the Anthropic API provider: half the price of direct calls, results within 24 hours
(usually well under one). Extraction is not latency-sensitive, so it can wait for them.
"""

import os

import anthropic
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from anthropic.types.messages.batch_create_params import Request

from synapptic.providers import ANTHROPIC_MAX_TOKENS


def batch_client(api_key: str) -> anthropic.Anthropic:
    """SDK client for the configured key, or ANTHROPIC_API_KEY from the environment."""
    return anthropic.Anthropic(api_key=api_key or os.environ.get("ANTHROPIC_API_KEY") or None)


def submit_batch(requests: dict[str, tuple[str, str]], api_key: str) -> str:
    """Submit {custom_id: (model, prompt)} as one Message Batch. Returns the batch id.

    custom_id must match ^[a-zA-Z0-9_-]{1,64}$.
    """
    batch = batch_client(api_key).messages.batches.create(requests=[
        Request(custom_id=custom_id, params=MessageCreateParamsNonStreaming(
            model=model,
            max_tokens=ANTHROPIC_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
        ))
        for custom_id, (model, prompt) in requests.items()
    ])
    return batch.id


def batch_results(batch_id: str, api_key: str) -> dict[str, dict] | None:
    """None while the batch is still processing. Once ended: {custom_id: {"text", "usage", "error"}}.

    text is None when the request errored, expired, was canceled or refused; error names why.
    Results arrive in any order, so they are keyed by custom_id.
    """
    client = batch_client(api_key)
    if client.messages.batches.retrieve(batch_id).processing_status != "ended":
        return None
    results = {}
    for entry in client.messages.batches.results(batch_id):
        if entry.result.type != "succeeded":
            results[entry.custom_id] = {"text": None, "usage": {}, "error": entry.result.type}
            continue
        message = entry.result.message
        refused = message.stop_reason == "refusal"
        text = "".join(block.text for block in message.content if block.type == "text").strip()
        results[entry.custom_id] = {"text": None if refused else (text or None),
                                    "usage": message.usage.model_dump(),
                                    "error": "refusal" if refused else None}
    return results
