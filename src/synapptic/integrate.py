"""Write Tier 3 archetypes to AI coding assistant memory/config systems.

Supports multiple output targets with per-model guard filtering.
Each target maps to a model family — guards marked redundant/backfire for
that family are excluded from the archetype written to that target.
"""

from pathlib import Path

from synapptic.config import CLAUDE_PROJECTS_DIR
from synapptic.outputs import WRITERS, resolve_target_model, write_claude_code
from synapptic.providers import load_config
from synapptic.state import slug_from_project_dir, load_archetype, load_profile
from synapptic.synthesize import filter_for_narrative
from synapptic.benchmark import remove_guard_from_archetype


def _combine_profiles(global_profile: dict, project_profile: dict) -> dict:
    """Build a merged view so model resolution can see both global and project verdicts."""
    dimensions = {}
    for dim_name, prefs in global_profile.get("dimensions", {}).items():
        dimensions[dim_name] = list(prefs)
    for dim_name, prefs in project_profile.get("dimensions", {}).items():
        dimensions.setdefault(dim_name, []).extend(prefs)
    return {"dimensions": dimensions}


def _removed_rules_for_target(profile: dict, target_model: str | None) -> list[str]:
    """Return rule observations that should be excluded for this target model."""
    if not target_model:
        return []

    full = filter_for_narrative(profile)
    filtered = filter_for_narrative(profile, target_model=target_model)

    def _obs_by_dim(p: dict) -> dict[str, set[str]]:
        return {
            dim: {x.get("observation", "") for x in prefs if x.get("observation")}
            for dim, prefs in p.get("dimensions", {}).items()
        }

    full_obs = _obs_by_dim(full)
    filtered_obs = _obs_by_dim(filtered)

    removed = []
    for dim, texts in full_obs.items():
        # Only these dims generate prescriptive rules that may appear in archetypes.
        if dim not in ("guards", "ai_failures"):
            continue
        removed.extend(sorted(texts - filtered_obs.get(dim, set())))
    return removed


def _apply_target_filter(archetype_md: str | None, profile: dict, target_model: str | None) -> tuple[str, int]:
    """Remove model-excluded rules from an archetype markdown document."""
    if not archetype_md:
        return "", 0

    removed_rules = _removed_rules_for_target(profile, target_model)
    filtered_md = archetype_md
    for rule in removed_rules:
        filtered_md = remove_guard_from_archetype(filtered_md, rule)
    return filtered_md, len(removed_rules)


def integrate_archetypes(project_slugs: list[str] | None = None):
    """Write combined archetypes to all configured output targets.

    For each target, resolves the best matching model from benchmark verdicts
    and filters out redundant/backfire guards for that model.

    Returns list of (slug, target, path, target_model, excluded_count) tuples.
    """
    config = load_config()
    output_targets = config.get("outputs", ["claude-code"])
    global_archetype = load_archetype(project_slug=None) or ""
    global_profile = load_profile(project_slug=None)

    project_dirs = find_project_dirs_with_slugs()

    if project_slugs:
        project_dirs = {
            slug: info for slug, info in project_dirs.items()
            if slug in project_slugs
        }

    updated = []
    for slug, info in project_dirs.items():
        project_archetype = load_archetype(project_slug=slug)
        project_profile = load_profile(project_slug=slug)
        combined_profile = _combine_profiles(global_profile, project_profile)

        for target in output_targets:
            # Resolve target model for guard filtering
            target_model = resolve_target_model(target, combined_profile)

            filtered_global, excluded_global = _apply_target_filter(global_archetype, global_profile, target_model)
            filtered_project, excluded_project = _apply_target_filter(project_archetype, project_profile, target_model)
            excluded_count = excluded_global + excluded_project

            combined = combine_archetypes(filtered_global, filtered_project, slug)
            if not combined.strip():
                continue

            if target == "claude-code":
                memory_dir = info.get("memory_dir")
                if memory_dir and memory_dir.exists():
                    write_claude_code(combined, memory_dir)
                    updated.append((slug, "claude-code", memory_dir, target_model, excluded_count))

            elif target in WRITERS:
                project_root = info.get("project_root")
                if project_root and project_root.exists():
                    writer = WRITERS[target]
                    writer(combined, project_root)
                    updated.append((slug, target, project_root, target_model, excluded_count))

    return updated


def combine_archetypes(global_md: str, project_md: str | None, project_slug: str) -> str:
    """Combine global and project-specific archetypes into one document."""
    parts = []
    if global_md:
        parts.append(global_md)
    if project_md:
        parts.append(f"\n---\n\n*Project-specific for `{project_slug}`:*\n\n{project_md}")
    return "\n".join(parts)


def find_project_dirs_with_slugs() -> dict[str, dict]:
    """Find all Claude Code project directories with their slugs and resolved paths."""
    if not CLAUDE_PROJECTS_DIR.exists():
        return {}

    result = {}
    for proj in CLAUDE_PROJECTS_DIR.iterdir():
        if not proj.is_dir():
            continue
        slug = slug_from_project_dir(proj.name)
        memory_dir = proj / "memory"
        project_root = resolve_project_root(proj.name)

        info = {}
        if memory_dir.exists():
            info["memory_dir"] = memory_dir
        if project_root and project_root.exists():
            info["project_root"] = project_root
        if info:
            result[slug] = info

    return result


def resolve_project_root(encoded_dir_name: str) -> Path | None:
    """Resolve the actual filesystem path from a Claude Code encoded directory name.

    Claude Code encodes paths: /home/user/projects/app → -home-user-projects-app
    with _ encoded as --, spaces dropped.

    Returns None if the path does not exist, is not a directory, or resolves
    outside the user's home directory (security boundary).
    """
    name = encoded_dir_name.strip("-")
    # -- represents underscore, - represents /
    path_str = name.replace("--", "\x00").replace("-", "/").replace("\x00", "_")
    path = Path("/" + path_str).resolve()

    # Security: reject paths outside user's home directory
    # Use relative_to() not startswith() — startswith("/home/alice") passes "/home/alice_evil"
    home = Path.home().resolve()
    try:
        path.relative_to(home)
    except ValueError:
        return None

    if path.exists() and path.is_dir():
        return path
    return None
