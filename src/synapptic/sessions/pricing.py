"""Claude prices per million tokens (USD), for the cost of a session's API responses.

Rates: platform.claude.com/docs/en/about-claude/pricing, fetched 2026-11-01. A model id with a date or other
suffix (claude-haiku-4-5-20251001) resolves to its longest matching key, see price_for().
"""


def claude_rates(input_rate: float, output_rate: float, cache_read: float, write_5m: float, write_1h: float) -> dict:
    return {"input": input_rate, "output": output_rate, "cache_read": cache_read,
            "cache_write_5m": write_5m, "cache_write_1h": write_1h}


MODEL_PRICING = {
    "claude-fable-5-1": claude_rates(10.0, 50.0, 0.25, 12.50, 20.0),
    "claude-mythos-5-1": claude_rates(10.0, 50.0, 0.25, 12.50, 20.0),
    "claude-fable-5": claude_rates(10.0, 50.0, 1.0, 12.50, 20.0),
    "claude-mythos-5": claude_rates(10.0, 50.0, 1.0, 12.50, 20.0),
    "claude-opus-5-5": claude_rates(4.0, 20.0, 0.20, 5.0, 8.0),
    "claude-opus-5": claude_rates(5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-8": claude_rates(5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-7": claude_rates(5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-6": claude_rates(5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-5": claude_rates(5.0, 25.0, 0.50, 6.25, 10.0),
    "claude-opus-4-1": claude_rates(15.0, 75.0, 1.50, 18.75, 30.0),
    "claude-opus-4": claude_rates(15.0, 75.0, 1.50, 18.75, 30.0),
    "claude-sonnet-5-5": claude_rates(2.0, 10.0, 0.10, 2.50, 4.0),
    "claude-sonnet-5": claude_rates(2.0, 10.0, 0.20, 2.50, 4.0),
    "claude-sonnet-4-6": claude_rates(3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-sonnet-4-5": claude_rates(3.0, 15.0, 0.30, 3.75, 6.0),
    "claude-sonnet-4": claude_rates(3.0, 15.0, 0.30, 3.75, 6.0),
    # Haiku 5.5 is priced by prompt size: these rates up to 100,000 prompt tokens, "long_prompt" above
    "claude-haiku-5-5": {**claude_rates(0.10, 0.50, 0.01, 0.125, 0.20),
                         "long_prompt": {"over": 100_000, **claude_rates(0.50, 2.50, 0.05, 0.625, 1.0)}},
    "claude-haiku-4-5": claude_rates(1.0, 5.0, 0.10, 1.25, 2.0),
    "claude-3-5-haiku": claude_rates(0.80, 4.0, 0.08, 1.0, 1.60),
}

# Fallback for a model the table does not know
DEFAULT_PRICING = {"input": 3.0, "output": 15.0}


def price_for(model: str) -> dict:
    """The rates for a model id: an exact key, else the longest key the id starts with, else DEFAULT_PRICING."""
    if model in MODEL_PRICING:
        return MODEL_PRICING[model]
    matches = [key for key in MODEL_PRICING if model.startswith(f"{key}-")]
    return MODEL_PRICING[max(matches, key=len)] if matches else DEFAULT_PRICING
