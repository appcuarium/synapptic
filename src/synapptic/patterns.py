"""Extensible extraction patterns for synapptic.

A pattern is a directory containing a `prompt.md` file that defines how
to extract observations from session transcripts. The prompt uses
placeholders that get filled at runtime:

    {dimensions}                — active dimension definitions
    {existing_profile_section}  — current profile for skip-known-patterns context
    {transcript}                — filtered session content
    {extraction_focus}          — fresh vs profile-aware instructions
    {dimension_list}            — comma-separated active dimension names

Patterns are searched in order:
    1. ~/.synapptic/patterns/<name>/prompt.md   (user-created)
    2. Built-in patterns shipped with the package

Users create custom patterns with: synapptic patterns create <name>
"""

from pathlib import Path

from synapptic.config import SYNAPPTIC_DIR


PATTERNS_DIR = SYNAPPTIC_DIR / "patterns"

DEFAULT_TEMPLATE = """# Role

Analyze an AI coding assistant session transcript.

# Dimensions

{dimensions}

# Session Transcript

{transcript}

# Extraction Rules

{extraction_focus}

# Response Format

Respond with ONLY a JSON array:
[
  {{
    "dimension": "one of: {dimension_list}",
    "observation": "what you observed",
    "confidence": 0.85,
    "evidence": "brief quote or description"
  }}
]
"""


def list_patterns() -> list[dict]:
    """List all available patterns with their source."""
    found = {}

    # Built-in (lowest priority — user patterns override)
    builtin = builtin_patterns_dir()
    if builtin and builtin.exists():
        for d in builtin.iterdir():
            if d.is_dir() and (d / "prompt.md").exists():
                found[d.name] = {"name": d.name, "source": "built-in", "path": d}

    # User patterns (highest priority)
    if PATTERNS_DIR.exists():
        for d in PATTERNS_DIR.iterdir():
            if d.is_dir() and (d / "prompt.md").exists():
                found[d.name] = {"name": d.name, "source": "user", "path": d}

    return sorted(found.values(), key=lambda x: x["name"])


def load_pattern(name: str = "default") -> str | None:
    """Load a pattern's prompt content. User patterns override built-in."""
    user_path = PATTERNS_DIR / name / "prompt.md"
    if user_path.exists():
        return user_path.read_text()

    builtin = builtin_patterns_dir()
    if builtin:
        path = builtin / name / "prompt.md"
        if path.exists():
            return path.read_text()

    return None


def create_pattern(name: str) -> Path:
    """Create a new user pattern from the default template."""
    pattern_dir = PATTERNS_DIR / name
    pattern_dir.mkdir(parents=True, exist_ok=True)

    prompt_path = pattern_dir / "prompt.md"
    if not prompt_path.exists():
        base = load_pattern("default") or DEFAULT_TEMPLATE
        prompt_path.write_text(base)

    return prompt_path


def builtin_patterns_dir() -> Path | None:
    """Resolve the built-in patterns directory from the installed package."""
    try:
        from importlib.resources import files
        return Path(str(files("synapptic") / "bundle" / "patterns"))
    except (ImportError, TypeError):
        return None
