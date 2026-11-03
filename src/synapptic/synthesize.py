"""Tier 3: the narrative archetype the configured model writes from the weighted profile.

Which observations qualify and what the model is asked live in the synapptic-core package; this module
serialises the profile, calls the model and returns the document.
"""

import json
import sys

import yaml

from synapptic.core import require_core
from synapptic.providers import call_llm


def synthesize_archetype(profile: dict, model: str = "sonnet", config: dict | None = None, target_model: str | None = None) -> str | None:
    """The archetype document for a profile, or None when nothing qualifies or the model call failed.

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
    narrative = call_llm(require_core().synthesis_prompt(profile_yaml), config=config)
    if not narrative:
        return None
    return require_core().strip_fences(narrative)


def filter_for_narrative(profile: dict, target_model: str | None = None) -> dict:
    """The profile's preferences strong enough for the narrative, by dimension."""
    return json.loads(require_core().filter_for_narrative(json.dumps(profile or {}), target_model))
