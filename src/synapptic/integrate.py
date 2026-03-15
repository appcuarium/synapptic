"""Write Tier 3 archetypes to AI coding assistant memory/config systems.

Supports multiple output targets: Claude Code, Cursor, Copilot, Gemini.
Each project gets a combined archetype: global sections + project-specific sections.
"""

from pathlib import Path

from synapptic.config import CLAUDE_PROJECTS_DIR
from synapptic.outputs import WRITERS, write_claude_code
from synapptic.providers import load_config
from synapptic.state import slug_from_project_dir, load_archetype


def integrate_archetypes(project_slugs: list[str] | None = None):
    """Write combined archetypes to all configured output targets.

    Returns list of (slug, target, path) tuples for what was written.
    """
    config = load_config()
    output_targets = config.get("outputs", ["claude-code"])
    global_archetype = load_archetype(project_slug=None) or ""

    project_dirs = find_project_dirs_with_slugs()

    if project_slugs:
        project_dirs = {
            slug: info for slug, info in project_dirs.items()
            if slug in project_slugs
        }

    updated = []
    for slug, info in project_dirs.items():
        project_archetype = load_archetype(project_slug=slug)
        combined = combine_archetypes(global_archetype, project_archetype, slug)

        if not combined.strip():
            continue

        for target in output_targets:
            if target == "claude-code":
                memory_dir = info.get("memory_dir")
                if memory_dir and memory_dir.exists():
                    write_claude_code(combined, memory_dir)
                    updated.append((slug, "claude-code", memory_dir))

            elif target in WRITERS:
                project_root = info.get("project_root")
                if project_root and project_root.exists():
                    writer = WRITERS[target]
                    writer(combined, project_root)
                    updated.append((slug, target, project_root))

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
    """
    name = encoded_dir_name.strip("-")
    # -- represents underscore, - represents /
    path_str = name.replace("--", "\x00").replace("-", "/").replace("\x00", "_")
    path = Path("/" + path_str)

    if path.exists() and path.is_dir():
        return path
    return None
