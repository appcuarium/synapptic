"""Tier 2: the weighted preference store, global and per project.

How observations accumulate, decay, merge and promote lives in the synapptic-core package; this module
passes profiles and observations through and formats the summary people read.
"""

import json
from datetime import datetime, timezone

from synapptic.core import require_core


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def merge_observations(existing_profile: dict, new_observations: list[dict], decay_factor: float | None = None) -> dict:
    """The profile with the observations merged in (decay applied, matches reinforced, the rest added)."""
    return json.loads(require_core().merge_observations(json.dumps(existing_profile), json.dumps(new_observations),
                                                        now_iso(), decay_factor))


def time_decay(last_seen: str, now: datetime) -> float:
    """Multiplier for a preference not seen for a while."""
    return require_core().time_decay(last_seen, now.isoformat())


def rebuild_profile(observations: list[dict], previous: dict, promotions: list[dict] = ()) -> dict:
    """A profile recomputed from every observation, once each, in the order they were extracted."""
    return json.loads(require_core().rebuild_profile(json.dumps(observations), json.dumps(previous), now_iso(),
                                                     json.dumps(list(promotions))))


def profile_changed(previous: dict, rebuilt: dict) -> bool:
    """True when the preferences differ, ignoring metadata and weight noise."""
    return require_core().profile_changed(json.dumps(previous), json.dumps(rebuilt))


def route_observations(observations: list[dict]) -> tuple[list[dict], list[dict]]:
    """(global observations, project-local observations)."""
    global_json, project_json = require_core().route_observations(json.dumps(observations))
    return json.loads(global_json), json.loads(project_json)


def promote_to_global(project_profiles: dict[str, dict], global_profile: dict) -> list[dict]:
    """Observations seen in enough projects, to add to the global profile."""
    return json.loads(require_core().promote_to_global(json.dumps(project_profiles), json.dumps(global_profile)))


def find_match(prefs: list[dict], observation_text: str, threshold: float = 0.7) -> int | None:
    """The index of the preference the text matches, or None."""
    return require_core().find_match(json.dumps(prefs), observation_text, threshold)


def profile_summary(profile: dict, label: str = "") -> str:
    """A human-readable summary of the profile."""
    dimensions = profile.get("dimensions", {})
    metadata = profile.get("metadata", {})

    header = f"{label} " if label else ""
    lines = [
        f"{header}Profile v{metadata.get('profile_version', 0)} "
        f"({metadata.get('total_sessions_analyzed', 0)} sessions analyzed)",
        f"Last updated: {metadata.get('last_updated', 'never')}",
        "",
    ]

    for dim_name, prefs in dimensions.items():
        lines.append(f"## {dim_name.title()} ({len(prefs)} preferences)")
        for p in prefs[:5]:  # Top 5 per dimension
            weight = p.get("weight", 0)
            count = p.get("evidence_count", 0)
            lines.append(f"  [{weight:.2f}] ({count}x) {p.get('observation', '')}")
        if len(prefs) > 5:
            lines.append(f"  ... and {len(prefs) - 5} more")
        lines.append("")

    return "\n".join(lines)
