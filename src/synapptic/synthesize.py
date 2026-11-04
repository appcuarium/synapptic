"""Tier 3: what the configured model writes from the weighted profile: the user's twin (the narrative every
session starts with) and the guards as rules for the harness.

Which observations qualify, what the model is asked and how its reply is split live in the synapptic-core
package; this module serialises the profile, calls the model and hands the two parts back.
"""

import json
import sys

import yaml

from synapptic.core import require_core
from synapptic.providers import call_llm


def synthesize_profile(profile: dict, config: dict | None = None, target_model: str | None = None,
                       existing_rules: list[dict] | None = None) -> dict | None:
    """{"twin": the narrative, "rules": [{id, text, phrases, files}]} for a profile, or None when nothing
    qualifies or the model call failed. `existing_rules` ({id, text}) lets the model keep a rule's id.

    target_model: if set, guards marked redundant/backfire for this model are left out.
    """
    filtered_profile = filter_for_narrative(profile, target_model=target_model)
    if not filtered_profile.get("dimensions"):
        total = sum(len(v) for v in profile.get("dimensions", {}).values())
        if total == 0:
            print("No observations in profile yet", file=sys.stderr)
        else:
            print(f"{total} observations in profile but none pass the narrative filter "
                  f"(need evidence_count >= 2 for user dimensions, weight >= 0.5 for guards). "
                  f"Process more sessions to reinforce patterns.", file=sys.stderr)
        return None

    profile_yaml = yaml.dump(filtered_profile, default_flow_style=False, sort_keys=False, allow_unicode=True)
    existing = json.dumps(existing_rules or [], ensure_ascii=False)
    reply = call_llm(require_core().synthesis_prompt(profile_yaml, existing), config=config)
    if not reply:
        return None
    parts = json.loads(require_core().synthesis_parts(reply))
    return parts if parts.get("twin") else None


def synthesize_archetype(profile: dict, model: str = "sonnet", config: dict | None = None, target_model: str | None = None) -> str | None:
    """The twin alone, for callers that only write the archetype."""
    parts = synthesize_profile(profile, config=config, target_model=target_model)
    return parts["twin"] if parts else None


def filter_for_narrative(profile: dict, target_model: str | None = None) -> dict:
    """The profile's preferences strong enough for the narrative, by dimension."""
    return json.loads(require_core().filter_for_narrative(json.dumps(profile or {}), target_model))
