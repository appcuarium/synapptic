"""Tier 3 narrative archetype generation via configurable provider."""

import yaml

from synapptic.config import NARRATIVE_MIN_EVIDENCE, NARRATIVE_MIN_WEIGHT
from synapptic.providers import call_llm


SYNTHESIS_PROMPT = """You are converting a structured user profile into a document that will be loaded
at the start of every Claude Code session. This document has TWO jobs:

1. **Profile**: Tell Claude who the user is so it can adapt its behavior
2. **Guards**: Give Claude concrete rules to follow, derived from observed failures

## Weighted User Profile

{profile_yaml}

## Instructions

Generate a markdown document with these exact sections:

### Section 1: ## User Archetype
A concise narrative (300-500 words) describing who this user is:
- Expertise, communication style, workflow patterns, values
- Write in second person ("You are working with...")
- Lead with the most important observations
- Be specific — use concrete examples from evidence

### Section 2: ## Guards
A numbered list of concrete behavioral rules for Claude. These are NOT suggestions —
they are hard rules derived from observed failures and user corrections. Format:

```
1. **ALWAYS [action]** — [why, traced to observed failure]
2. **NEVER [action]** — [why, traced to observed failure]
3. **BEFORE [action], first [prerequisite]** — [why]
4. **WHEN [condition], [required response]** — [why]
5. **IF [situation], do NOT [bad approach] — instead [correct approach]** — [why]
```

Rules for writing guards:
- Each guard must trace to a specific observed failure or user correction
- Phrase as imperatives, not suggestions
- Be specific enough that Claude can mechanically follow the rule
- Order by severity: rules that prevent user frustration first, then efficiency rules
- Deduplicate — if two failures suggest the same guard, write one strong guard
- Include the "why" so Claude can apply judgment in edge cases
- Do NOT pad with generic best practices — only include guards backed by evidence

### Section 3: ## Known Weaknesses
A short list (3-8 items) of Claude behavioral patterns that repeatedly caused problems
in sessions with this user. Not generic Claude weaknesses — specific patterns observed
in THIS user's sessions. Example:

- Tends to over-explain after making changes (user wants terse confirmation)
- Forgets prior instructions mid-session when context gets long
- Makes unrequested scope expansions (touching code adjacent to the task)

### Formatting rules
- Do NOT include weight numbers, evidence counts, or YAML artifacts
- Do NOT include any frontmatter
- Total length: 800-1500 words
- Start with ## User Archetype

Output ONLY the markdown document."""


def synthesize_archetype(profile: dict, model: str = "sonnet", config: dict | None = None) -> str | None:
    """Generate a narrative archetype from the weighted profile.

    Only includes preferences with weight > threshold and evidence_count >= minimum.
    """
    filtered_profile = filter_for_narrative(profile)

    if not filtered_profile.get("dimensions"):
        return None

    profile_yaml = yaml.dump(
        filtered_profile,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )

    prompt = SYNTHESIS_PROMPT.format(profile_yaml=profile_yaml)

    narrative = call_llm(prompt, config=config)
    if not narrative:
        return None

    # Strip markdown fences if present
    if narrative.startswith("```"):
        lines = narrative.split("\n")
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        narrative = "\n".join(lines)

    return narrative.strip()


def filter_for_narrative(profile: dict) -> dict:
    """Filter profile to only include high-confidence, well-evidenced preferences.

    Guards and ai_failures use a lower evidence threshold because they're
    valuable even from a single session — a concrete failure is worth acting on
    immediately, unlike a personality trait that needs reinforcement.
    """
    dimensions = profile.get("dimensions", {})
    filtered = {}

    # Prescriptive dimensions: guards, ai_failures — high-confidence single
    # observations are actionable immediately
    prescriptive_dims = {"guards", "ai_failures"}

    for dim_name, prefs in dimensions.items():
        if dim_name in prescriptive_dims:
            # For guards/failures: include if weight >= 0.5 (no evidence_count gate)
            strong_prefs = [
                p for p in prefs
                if p.get("weight", 0) >= 0.5
            ]
        else:
            # For profile dimensions: require multiple sessions of evidence
            strong_prefs = [
                p for p in prefs
                if p.get("weight", 0) >= NARRATIVE_MIN_WEIGHT
                and p.get("evidence_count", 0) >= NARRATIVE_MIN_EVIDENCE
            ]
        if strong_prefs:
            filtered[dim_name] = strong_prefs

    return {"dimensions": filtered}
