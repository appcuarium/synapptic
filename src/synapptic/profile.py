"""Tier 2 weighted preference accumulation — pure Python, no LLM.

Merges new observations into the weighted preference store with
exponential decay, conflict resolution, and archival.

Supports two-level profiles: project-specific and global.
Observations in GLOBAL_DIMENSIONS are always routed to global.
Observations in PROJECT_DIMENSIONS stay project-local.
Observations in MIXED_DIMENSIONS start project-local and promote
to global when seen across multiple projects.
"""

import math
from datetime import datetime, timezone
from difflib import SequenceMatcher

from synapptic.config import (
    DEFAULT_DECAY_FACTOR,
    GLOBAL_DIMENSIONS,
    GLOBAL_PROMOTION_MIN_PROJECTS,
    MIN_WEIGHT_THRESHOLD,
    MIXED_DIMENSIONS,
    TIME_DECAY_HALFLIFE_DAYS,
    PROJECT_DIMENSIONS,
)


def merge_observations(
    existing_profile: dict,
    new_observations: list[dict],
    decay_factor: float = DEFAULT_DECAY_FACTOR,
) -> dict:
    """Merge new observations into the weighted preference store.

    For each observation:
    1. Find matching existing preference (same dimension + similar text)
       - If match: increment evidence_count, update weight, refresh last_seen
       - If new: add with initial weight based on confidence
    2. Apply decay to ALL existing preferences (weight *= decay_factor)
    3. Archive preferences with weight < threshold

    Returns updated profile dict.
    """
    dimensions = existing_profile.get("dimensions", {})
    metadata = existing_profile.get("metadata", {
        "total_sessions_analyzed": 0,
        "last_updated": None,
        "profile_version": 0,
    })

    # Step 1: Apply decay to all existing preferences
    # Two components: (a) per-merge multiplicative decay, (b) time-based half-life
    # Store pre-decay weights so step 2 can blend without divide-induced rounding errors
    now = datetime.now(timezone.utc)
    pre_decay_weights = {}
    for dim_name, prefs in dimensions.items():
        for pref in prefs:
            # Use observation text as stable key — id() breaks on deep-copy/reconstruction
            stable_key = (dim_name, pref.get("observation", ""))
            pre_decay_weights[stable_key] = pref.get("weight", 0.5)
            pref["weight"] = pref.get("weight", 0.5) * decay_factor
            last_seen = pref.get("last_seen", "")
            if last_seen and TIME_DECAY_HALFLIFE_DAYS > 0:
                try:
                    last_dt = datetime.fromisoformat(last_seen)
                    days_ago = (now - last_dt).days
                    if days_ago > 0:
                        pref["weight"] *= math.pow(0.5, days_ago / TIME_DECAY_HALFLIFE_DAYS)
                except (ValueError, TypeError):
                    pass

    # Step 2: Merge each new observation
    session_ids_seen = set()
    for obs in new_observations:
        dim = obs.get("dimension", "")
        if not dim:
            continue

        if dim not in dimensions:
            dimensions[dim] = []

        session_id = obs.get("session_id", "")
        session_ids_seen.add(session_id)

        match_idx = find_match(dimensions[dim], obs["observation"])

        if match_idx is not None:
            # Reinforce existing preference
            existing = dimensions[dim][match_idx]
            existing["evidence_count"] = existing.get("evidence_count", 1) + 1
            # Boost weight: blend pre-decay weight with new confidence
            stable_key = (dim, existing.get("observation", ""))
            old_weight = pre_decay_weights.get(stable_key, existing["weight"])
            existing["weight"] = min(
                1.0,
                old_weight * 0.7 + obs.get("confidence", 0.5) * 0.3,
            )
            existing["last_seen"] = obs.get("timestamp", "")
            # Add source session (cap at 10 most recent to prevent unbounded growth)
            sources = existing.get("sources", [])
            if session_id and session_id not in sources:
                sources.append(session_id)
                if len(sources) > 10:
                    sources = sources[-10:]
            existing["sources"] = sources
            # Track project origin
            project = obs.get("project", "")
            if project:
                projects = existing.get("projects", [])
                if project not in projects:
                    projects.append(project)
                existing["projects"] = projects
            # Update observation text if new one is better (longer, more specific)
            if len(obs["observation"]) > len(existing["observation"]):
                existing["observation"] = obs["observation"]
        else:
            # New preference
            now = obs.get("timestamp", datetime.now(timezone.utc).isoformat())
            project = obs.get("project", "")
            dimensions[dim].append({
                "observation": obs["observation"],
                "weight": obs.get("confidence", 0.5),
                "evidence_count": 1,
                "first_seen": now,
                "last_seen": now,
                "sources": [session_id] if session_id else [],
                "projects": [project] if project else [],
            })

    # Step 3: Archive low-weight preferences
    for dim_name in list(dimensions.keys()):
        dimensions[dim_name] = [
            p for p in dimensions[dim_name]
            if p.get("weight", 0) >= MIN_WEIGHT_THRESHOLD
        ]
        # Remove empty dimensions
        if not dimensions[dim_name]:
            del dimensions[dim_name]

    # Step 4: Sort each dimension by weight (highest first)
    for dim_name in dimensions:
        dimensions[dim_name].sort(key=lambda p: p.get("weight", 0), reverse=True)

    # Update metadata
    metadata["total_sessions_analyzed"] = metadata.get("total_sessions_analyzed", 0) + len(session_ids_seen)
    metadata["last_updated"] = datetime.now(timezone.utc).isoformat()
    metadata["profile_version"] = metadata.get("profile_version", 0) + 1

    return {"dimensions": dimensions, "metadata": metadata}


def route_observations(observations: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split observations into global and project-local lists.

    - GLOBAL_DIMENSIONS → always global
    - PROJECT_DIMENSIONS → always project-local
    - MIXED_DIMENSIONS → project-local (may promote later via promote_to_global)

    Returns (global_observations, project_observations).
    """
    global_obs = []
    project_obs = []

    for obs in observations:
        dim = obs.get("dimension", "")
        if dim in GLOBAL_DIMENSIONS:
            global_obs.append(obs)
        else:
            project_obs.append(obs)

    return global_obs, project_obs


def promote_to_global(project_profiles: dict[str, dict], global_profile: dict) -> list[dict]:
    """Find observations in MIXED_DIMENSIONS that appear across multiple projects.

    Returns list of observations to add to the global profile.
    """
    # Collect all observations from mixed dimensions across projects
    # grouped by normalized text
    candidates = {}  # observation_text -> {projects: set, best_obs: dict}

    for project_slug, profile in project_profiles.items():
        for dim_name, prefs in profile.get("dimensions", {}).items():
            if dim_name not in MIXED_DIMENSIONS:
                continue
            for pref in prefs:
                text = pref["observation"].lower().strip()
                # Check similarity against existing candidates
                matched = False
                for key, data in candidates.items():
                    if SequenceMatcher(None, text, key).ratio() >= 0.6:
                        data["projects"].add(project_slug)
                        if pref.get("weight", 0) > data["best_obs"].get("weight", 0):
                            data["best_obs"] = pref
                        matched = True
                        break
                if not matched:
                    candidates[text] = {
                        "projects": {project_slug},
                        "best_obs": pref,
                        "dimension": dim_name,
                    }

    # Promote candidates seen in enough projects
    promotions = []
    for text, data in candidates.items():
        if len(data["projects"]) >= GLOBAL_PROMOTION_MIN_PROJECTS:
            obs = data["best_obs"].copy()
            obs["projects"] = list(data["projects"])
            obs["dimension"] = data["dimension"]
            # Check if already in global profile
            global_dims = global_profile.get("dimensions", {})
            global_prefs = global_dims.get(data["dimension"], [])
            if find_match(global_prefs, obs["observation"]) is None:
                promotions.append(obs)

    return promotions


def find_match(prefs: list[dict], observation_text: str, threshold: float = 0.7) -> int | None:
    """Find existing preference that matches the new observation.

    Uses SequenceMatcher (Ratcliff/Obershelp) for similarity. Returns the index
    of the best match, or None if no match exceeds the threshold.

    Threshold 0.7 was chosen empirically: 0.6 produces false merges (e.g.,
    "prefer tabs" matches "prefer spaces"), while 0.8 misses obvious paraphrases
    (e.g., "always read files first" vs "reads files before modifying").
    SequenceMatcher was chosen over Jaccard because observation texts are short
    sentences where word order matters.
    """
    best_ratio = 0.0
    best_idx = None

    obs_lower = observation_text.lower()

    for i, pref in enumerate(prefs):
        existing_lower = pref.get("observation", "").lower()
        ratio = SequenceMatcher(None, obs_lower, existing_lower).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_idx = i

    if best_ratio >= threshold:
        return best_idx
    return None


def profile_summary(profile: dict, label: str = "") -> str:
    """Generate a human-readable summary of the current profile."""
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
