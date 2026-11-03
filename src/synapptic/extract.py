"""Observation extraction: the configured model reads a filtered session and answers with observations.

The prompt and the reading of the answer live in the synapptic-core package; this module loads the user's
pattern if there is one, calls the model, and stamps the observations with the session and the time.
"""

import json
import sys
from datetime import datetime, timezone

from synapptic.core import require_core
from synapptic.filter import Turn, turns_to_text
from synapptic.patterns import load_pattern
from synapptic.providers import call_llm


def get_active_dimensions(config: dict | None = None) -> list[str]:
    """The dimensions in play for the configured profiling mode."""
    return list(require_core().active_dimensions((config or {}).get("profiling_mode", "both")))


def build_profile_summary(profile: dict) -> str:
    """The strongest existing observations, as the extraction prompt shows them."""
    return require_core().build_profile_summary(json.dumps(profile or {}))


def build_extraction_prompt(filtered_turns: list[Turn], profile: dict | None = None,
                            config: dict | None = None) -> str:
    """The extraction prompt: the user's pattern when configured and found, the built-in one otherwise."""
    pattern_name = (config or {}).get("pattern", "default")
    template = load_pattern(pattern_name)
    if template is None and pattern_name != "default":
        print(f"Pattern '{pattern_name}' not found, using default", file=sys.stderr)
    return require_core().build_extraction_prompt(
        turns_to_text(filtered_turns),
        json.dumps(profile) if profile else None,
        (config or {}).get("profiling_mode", "both"),
        template,
    )


def extract_observations(
    filtered_turns: list[Turn],
    session_id: str,
    model: str = "sonnet",
    profile: dict | None = None,
    config: dict | None = None,
) -> list[dict]:
    """The observations of a filtered transcript ([] when the model call or its parsing failed)."""
    return try_extract_observations(filtered_turns, session_id, model, profile, config) or []


def try_extract_observations(
    filtered_turns: list[Turn],
    session_id: str,
    model: str = "sonnet",
    profile: dict | None = None,
    config: dict | None = None,
) -> list[dict] | None:
    """Like extract_observations, but None when the model call or its parsing failed, so a failed session is
    not recorded as processed and is retried on the next run."""
    if not filtered_turns:
        return []
    prompt = build_extraction_prompt(filtered_turns, profile=profile, config=config)
    raw_output = call_llm(prompt, config=config)
    if not raw_output:
        return None
    return parse_observations(raw_output, session_id)


def parse_observations(raw_output: str, session_id: str) -> list[dict] | None:
    """A model's extraction reply as validated observations; None if it is not a JSON array.

    Shared by direct calls and Message Batches results, so both are read the same way.
    """
    return require_core().parse_observations(raw_output, session_id, datetime.now(timezone.utc).isoformat())


def extract_json(text: str) -> str:
    """The JSON array inside a reply that may carry markdown fences or trailing commentary."""
    return require_core().extract_json(text)
