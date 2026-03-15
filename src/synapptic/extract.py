"""LLM observation extraction via configurable provider and pattern."""

import json
import sys
from datetime import datetime, timezone

from synapptic.config import DIMENSIONS, DIMENSION_DESCRIPTIONS
from synapptic.filter import Turn, turns_to_text
from synapptic.patterns import load_pattern
from synapptic.providers import call_llm

# Instructions when a profile already exists
EXTRACTION_FOCUS_WITH_PROFILE = """Extract ONLY observations that are NEW or that REFINE the existing profile:
- New patterns not already covered by the existing observations below
- Refinements or corrections to existing observations (e.g., a guard that needs to be more specific)
- New failures and guards not already captured
- Contradictions to existing patterns (flag these explicitly with confidence reflecting how strong the contradiction is)

Do NOT re-extract patterns already well-established in the profile. Focus your budget on genuinely novel signal."""

# Instructions when no profile exists yet (first run)
EXTRACTION_FOCUS_FRESH = """Extract 5-25 observations from this transcript."""


def build_profile_summary(profile: dict) -> str:
    """Build a concise summary of the existing profile for the extraction prompt.

    Only includes high-weight observations to keep the prompt compact.
    """
    dims = profile.get("dimensions", {})
    if not dims:
        return ""

    lines = ["## Existing profile (already established — do NOT re-extract these)\n"]
    for dim_name, prefs in dims.items():
        # Only include top preferences per dimension (weight >= 0.7)
        top = [p for p in prefs if p.get("weight", 0) >= 0.7]
        if not top:
            continue
        lines.append(f"### {dim_name}")
        for p in top[:8]:  # Cap at 8 per dimension to keep prompt size reasonable
            w = p.get("weight", 0)
            ec = p.get("evidence_count", 0)
            lines.append(f"- [{w:.2f}, {ec}x] {p['observation']}")
        lines.append("")

    return "\n".join(lines)


def get_active_dimensions(config: dict | None = None) -> list[str]:
    """Return the list of active dimensions based on profiling mode."""
    from synapptic.config import USER_DIMENSIONS, AGENT_DIMENSIONS

    mode = (config or {}).get("profiling_mode", "both")
    if mode == "user":
        return [d for d in DIMENSIONS if d in USER_DIMENSIONS or d in ("expectations", "triggers")]
    elif mode == "agent":
        return [d for d in DIMENSIONS if d in AGENT_DIMENSIONS or d in ("expectations", "triggers")]
    return list(DIMENSIONS)


def build_extraction_prompt(filtered_turns: list[Turn], profile: dict | None = None,
                            config: dict | None = None) -> str:
    """Build the extraction prompt from the active pattern.

    Loads the pattern's prompt.md template and fills placeholders.
    If a profile exists, includes it so the LLM skips known patterns.
    """
    pattern_name = (config or {}).get("pattern", "default")
    template = load_pattern(pattern_name)
    if not template:
        print(f"Pattern '{pattern_name}' not found, using default", file=sys.stderr)
        template = load_pattern("default")
    if not template:
        raise RuntimeError("No extraction pattern found — reinstall synapptic")

    active_dims = get_active_dimensions(config)
    dimensions_text = "\n".join(
        f"- **{dim}**: {DIMENSION_DESCRIPTIONS[dim]}"
        for dim in active_dims
    )
    transcript_text = turns_to_text(filtered_turns)
    transcript_text = transcript_text.replace("{", "{{").replace("}", "}}")

    has_profile = profile and profile.get("dimensions")
    existing_profile_section = build_profile_summary(profile) if has_profile else ""
    if existing_profile_section:
        existing_profile_section = existing_profile_section.replace("{", "{{").replace("}", "}}")
    extraction_focus = EXTRACTION_FOCUS_WITH_PROFILE if has_profile else EXTRACTION_FOCUS_FRESH

    return template.format(
        dimensions=dimensions_text,
        existing_profile_section=existing_profile_section,
        transcript=transcript_text,
        extraction_focus=extraction_focus,
        dimension_list=", ".join(active_dims),
    )


def extract_observations(
    filtered_turns: list[Turn],
    session_id: str,
    model: str = "sonnet",
    profile: dict | None = None,
    config: dict | None = None,
) -> list[dict]:
    """Extract user observations from a filtered transcript via configured LLM provider.

    If profile is provided, the extraction prompt includes existing observations
    so the LLM can skip known patterns and focus on novel signal.

    Returns list of observation dicts with session_id and timestamp added.
    """
    if not filtered_turns:
        return []

    prompt = build_extraction_prompt(filtered_turns, profile=profile, config=config)

    raw_output = call_llm(prompt, config=config)
    if not raw_output:
        return []

    # Parse JSON response — handle markdown fences if present
    json_text = extract_json(raw_output)
    try:
        observations = json.loads(json_text)
    except json.JSONDecodeError as e:
        print(f"Failed to parse extraction response as JSON: {e}", file=sys.stderr)
        print(f"Raw output:\n{raw_output[:500]}", file=sys.stderr)
        return []

    if not isinstance(observations, list):
        print(f"Expected JSON array, got {type(observations)}", file=sys.stderr)
        return []

    # Validate and enrich each observation
    now = datetime.now(timezone.utc).isoformat()
    valid = []
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        if obs.get("dimension") not in DIMENSIONS:
            continue
        if not obs.get("observation"):
            continue

        obs.setdefault("confidence", 0.5)
        obs["session_id"] = session_id
        obs["timestamp"] = now
        valid.append(obs)

    return valid


def extract_json(text: str) -> str:
    """Extract JSON array from text that might have markdown fences or trailing commentary."""
    text = text.strip()

    # Remove markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    # Find the outermost JSON array boundaries — handles trailing text
    start = text.find("[")
    end = text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]

    return text.strip()
