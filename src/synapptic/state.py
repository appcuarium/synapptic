"""Read/write state directory at ~/.claude/synaptic/.

Supports two-level storage: global profile + per-project profiles.
Sessions are routed to projects based on which Claude Code project directory
they live in.
"""

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import yaml

from synapptic.config import (
    SYNAPPTIC_DIR,
    CLAUDE_PROJECTS_DIR,
    GLOBAL_DIR,
    PROFILE_HISTORY_DIR,
    PROJECTS_DIR,
    QUEUE_PATH,
)


# ---------------------------------------------------------------------------
# Directory management + migration
# ---------------------------------------------------------------------------

def ensure_dirs():
    """Create state directory structure."""
    SYNAPPTIC_DIR.mkdir(parents=True, exist_ok=True)
    GLOBAL_DIR.mkdir(exist_ok=True)
    (GLOBAL_DIR / "observations").mkdir(exist_ok=True)
    PROJECTS_DIR.mkdir(exist_ok=True)
    PROFILE_HISTORY_DIR.mkdir(exist_ok=True)


def ensure_project_dirs(project_slug: str):
    """Create per-project directory structure."""
    proj = PROJECTS_DIR / project_slug
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "observations").mkdir(exist_ok=True)


def project_dir(project_slug: str) -> Path:
    return PROJECTS_DIR / project_slug


# ---------------------------------------------------------------------------
# Profile read/write (global or per-project)
# ---------------------------------------------------------------------------

def empty_profile() -> dict:
    return {
        "dimensions": {},
        "metadata": {
            "total_sessions_analyzed": 0,
            "last_updated": None,
            "profile_version": 0,
        },
    }


def load_profile(project_slug: str | None = None) -> dict:
    """Load a profile. None = global, string = project-specific."""
    if project_slug:
        path = project_dir(project_slug) / "profile.yaml"
    else:
        path = GLOBAL_DIR / "profile.yaml"

    if not path.exists():
        return empty_profile()
    try:
        with open(path) as f:
            data = yaml.safe_load(f)
            if isinstance(data, dict):
                return data
            return empty_profile()
    except (yaml.YAMLError, OSError):
        return empty_profile()


def save_profile(profile: dict, project_slug: str | None = None):
    """Save a profile and snapshot to history. Uses atomic write."""
    ensure_dirs()

    if project_slug:
        ensure_project_dirs(project_slug)
        path = project_dir(project_slug) / "profile.yaml"
        label = project_slug
    else:
        path = GLOBAL_DIR / "profile.yaml"
        label = "global"

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    version = profile.get("metadata", {}).get("profile_version", 0)

    # Atomic write: write to temp file, then rename
    tmp_path = path.with_suffix(".yaml.tmp")
    with open(tmp_path, "w") as f:
        yaml.dump(profile, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    tmp_path.rename(path)

    history_path = PROFILE_HISTORY_DIR / f"{label}_{today}_v{version}.yaml"
    shutil.copy2(path, history_path)


# ---------------------------------------------------------------------------
# Archetype read/write (global or per-project)
# ---------------------------------------------------------------------------

def load_archetype(project_slug: str | None = None) -> str | None:
    """Load a narrative archetype. None = global."""
    if project_slug:
        path = project_dir(project_slug) / "archetype.md"
    else:
        path = GLOBAL_DIR / "archetype.md"

    if not path.exists():
        return None
    return path.read_text()


def save_archetype(content: str, project_slug: str | None = None):
    """Save a narrative archetype."""
    ensure_dirs()
    if project_slug:
        ensure_project_dirs(project_slug)
        path = project_dir(project_slug) / "archetype.md"
    else:
        path = GLOBAL_DIR / "archetype.md"
    path.write_text(content)


# ---------------------------------------------------------------------------
# Observations read/write
# ---------------------------------------------------------------------------

def load_observations(session_id: str, project_slug: str | None = None) -> list[dict] | None:
    """Load observations for a session.

    If project_slug is None, searches all project directories + global.
    """
    if project_slug:
        obs_path = project_dir(project_slug) / "observations" / f"{session_id}.json"
        if obs_path.exists():
            with open(obs_path) as f:
                return json.load(f)
        return None

    # Search all locations
    for search_dir in _all_observation_dirs():
        obs_path = search_dir / f"{session_id}.json"
        if obs_path.exists():
            with open(obs_path) as f:
                return json.load(f)
    return None


def save_observations(session_id: str, observations: list[dict], project_slug: str | None = None):
    """Save observations for a session under the correct project.

    Stores project_slug inside the JSON so it can be recovered later
    even if the original transcript is deleted.
    """
    ensure_dirs()
    if project_slug:
        ensure_project_dirs(project_slug)
        obs_path = project_dir(project_slug) / "observations" / f"{session_id}.json"
    else:
        obs_path = GLOBAL_DIR / "observations" / f"{session_id}.json"

    # Embed routing metadata so merge can find it without the transcript
    payload = {
        "project_slug": project_slug,
        "session_id": session_id,
        "observations": observations,
    }
    with open(obs_path, "w") as f:
        json.dump(payload, f, indent=2)


def get_processed_session_ids() -> set[str]:
    """Return all session IDs that have been extracted (across all projects + global)."""
    ids = set()
    for obs_dir in _all_observation_dirs():
        ids.update(p.stem for p in obs_dir.glob("*.json"))
    return ids


def find_all_observations() -> list[tuple[str, str | None, list[dict]]]:
    """Find all observation files with their project slugs.

    Returns list of (session_id, project_slug, observations).
    Reads project_slug from the JSON payload (not from directory path).
    Skips empty observations (extraction failures).
    """
    results = []
    for obs_dir in _all_observation_dirs():
        for obs_file in obs_dir.glob("*.json"):
            session_id = obs_file.stem
            try:
                with open(obs_file) as f:
                    data = json.load(f)
            except (json.JSONDecodeError, OSError):
                continue

            # Handle both old format (raw list) and new format (dict with metadata)
            if isinstance(data, dict) and "observations" in data:
                slug = data.get("project_slug")
                obs_list = data["observations"]
            elif isinstance(data, list):
                slug = None  # Legacy format — no project metadata
                obs_list = data
            else:
                continue

            # Skip empty (failed extractions)
            if not obs_list:
                continue

            results.append((session_id, slug, obs_list))
    return results


def _all_observation_dirs() -> list[Path]:
    """List all observation directories (global + per-project)."""
    dirs = []
    global_obs = GLOBAL_DIR / "observations"
    if global_obs.exists():
        dirs.append(global_obs)
    if PROJECTS_DIR.exists():
        for proj in PROJECTS_DIR.iterdir():
            if not proj.is_dir():
                continue
            obs_dir = proj / "observations"
            if obs_dir.exists():
                dirs.append(obs_dir)
    return dirs


# ---------------------------------------------------------------------------
# Session transcript discovery + project routing
# ---------------------------------------------------------------------------

def slug_from_project_dir(project_dir_name: str) -> str:
    """Derive a short project slug from a Claude Code project directory name.

    Claude Code encodes filesystem paths as directory names:
      /home/username/projects/my-app
      → -home-username-projects-my-app

    Encoding: / → -, _ → --, spaces dropped, literal - stays as -.
    We can't perfectly reverse this, so we use a heuristic:
    split on -- (underscore boundaries), take the last segment,
    then walk backwards through hyphen-parts stripping common path noise.
    """
    name = project_dir_name.strip("-")

    # -- represents underscores in the original path. Split to get rough segments.
    segments = name.split("--")
    if not segments:
        return "unknown"

    last = segments[-1].strip("-")
    if not last:
        return "unknown"

    # The last segment contains the deepest directory path encoded with hyphens.
    # Strip common OS/platform path components from the left.
    parts = last.split("-")
    noise = {"users", "home", "projects", "src", "code", "repos", "git",
             "work", "documents", "desktop", "downloads", "var", "opt",
             "usr", "lib", "local", "share", "volumes"}

    # Strip noise from the left
    meaningful = []
    found_meaningful = False
    for p in parts:
        if not found_meaningful and p.lower() in noise:
            continue
        found_meaningful = True
        meaningful.append(p)

    if not meaningful:
        return "home"

    # If result has repeated parts (parent/child with same name encoded as
    # "parent-child-child-sub"), find the repeat boundary and take the tail.
    if len(meaningful) > 3:
        for i in range(len(meaningful) - 1, 0, -1):
            if meaningful[i] in meaningful[:i]:
                meaningful = meaningful[i:]
                break

    # Strip trailing numeric parts (timestamps from worktree/session dirs)
    while meaningful and meaningful[-1].isdigit():
        meaningful.pop()

    # Cap at 4 parts max for readability
    if len(meaningful) > 4:
        meaningful = meaningful[-4:]

    if meaningful:
        return "-".join(meaningful)
    return last[:40] or "unknown"


def find_session_transcripts() -> dict[str, tuple[Path, str]]:
    """Find all session JSONL files across all Claude Code projects.

    Returns dict mapping session_uuid -> (path, project_slug).
    """
    transcripts = {}
    if not CLAUDE_PROJECTS_DIR.exists():
        return transcripts
    for proj in CLAUDE_PROJECTS_DIR.iterdir():
        if not proj.is_dir():
            continue
        slug = slug_from_project_dir(proj.name)
        for jsonl in proj.glob("*.jsonl"):
            session_id = jsonl.stem
            transcripts[session_id] = (jsonl, slug)
    return transcripts


def list_projects() -> list[str]:
    """List all project slugs that have synaptic data."""
    if not PROJECTS_DIR.exists():
        return []
    return sorted(
        d.name for d in PROJECTS_DIR.iterdir()
        if d.is_dir() and (d / "profile.yaml").exists()
    )


# ---------------------------------------------------------------------------
# Queue
# ---------------------------------------------------------------------------

def get_queue() -> list[str]:
    """Read queued session IDs from the processing queue."""
    if not QUEUE_PATH.exists():
        return []
    lines = QUEUE_PATH.read_text().strip().splitlines()
    return [line.strip() for line in lines if line.strip()]


def clear_queue():
    """Clear the processing queue."""
    if QUEUE_PATH.exists():
        QUEUE_PATH.write_text("")


# ---------------------------------------------------------------------------
# History + reset
# ---------------------------------------------------------------------------

def get_profile_history(project_slug: str | None = None) -> list[Path]:
    """List profile history snapshots, newest first."""
    if not PROFILE_HISTORY_DIR.exists():
        return []
    label = project_slug or "global"
    return sorted(PROFILE_HISTORY_DIR.glob(f"{label}_*.yaml"), reverse=True)


def reset_all():
    """Delete all synaptic state. Requires confirmation from caller."""
    if SYNAPPTIC_DIR.exists():
        shutil.rmtree(SYNAPPTIC_DIR)
    ensure_dirs()
