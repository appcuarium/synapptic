"""Multi-platform output writers for synapptic.

Writes the archetype to different AI coding assistant formats:
- Claude Code: ~/.claude/projects/*/memory/user_archetype.md (with YAML frontmatter)
- Cursor: .cursorrules or .cursor/rules/synapptic.mdc (with frontmatter)
- GitHub Copilot: .github/copilot-instructions.md
- Gemini: GEMINI.md
"""

import os
from pathlib import Path


def _atomic_write(path: Path, content: str) -> None:
    """Write content atomically: write to temp file, fsync, rename."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


# Available output targets
# model_family: used to filter guards by per-model benchmark verdicts.
# Matched against model_verdicts keys in profile.yaml (prefix match).
OUTPUTS = {
    "claude-code": {
        "name": "Claude Code",
        "description": "Writes to Claude Code memory system (~/.claude/projects/*/memory/)",
        "model_family": "claude",
    },
    "cursor": {
        "name": "Cursor",
        "description": "Writes to .cursor/rules/synapptic.mdc (project root)",
        "model_family": None,  # Cursor can use any model — no filtering
    },
    "copilot": {
        "name": "GitHub Copilot",
        "description": "Writes to .github/copilot-instructions.md (project root)",
        "model_family": "gpt",
    },
    "gemini": {
        "name": "Gemini Code Assist",
        "description": "Writes to .gemini/styleguide.md (project root)",
        "model_family": "gemini",
    },
    "codex": {
        "name": "OpenAI Codex CLI",
        "description": "Writes to AGENTS.md (project root)",
        "model_family": "gpt",
    },
    "windsurf": {
        "name": "Windsurf",
        "description": "Writes to .windsurfrules (project root)",
        "model_family": None,
    },
    "cline": {
        "name": "Cline",
        "description": "Writes to .clinerules (project root)",
        "model_family": None,
    },
    "aider": {
        "name": "Aider",
        "description": "Writes to CONVENTIONS.md (project root)",
        "model_family": None,
    },
    "continue": {
        "name": "Continue.dev",
        "description": "Writes to .continuerules (project root)",
        "model_family": None,
    },
}


def write_claude_code(archetype: str, memory_dir: Path) -> bool:
    """Write archetype to Claude Code memory system with YAML frontmatter."""
    frontmatter = (
        "---\n"
        "name: User archetype profile\n"
        "description: Auto-extracted coding preferences, workflow patterns, "
        "communication style, and expectations\n"
        "type: user\n"
        "---\n\n"
    )
    path = memory_dir / "user_archetype.md"
    _atomic_write(path, frontmatter + archetype)

    # Ensure MEMORY.md references it — insert near top so it's within the
    # 200-line cutoff (Claude Code truncates MEMORY.md after line 200)
    index_path = memory_dir / "MEMORY.md"
    if index_path.exists():
        content = index_path.read_text()
        entry = (
            "- [user_archetype.md](./user_archetype.md) "
            "— Auto-generated user archetype from session transcripts (synapptic)"
        )
        if "user_archetype.md" not in content:
            # Insert after the first heading line (# ...) or at the top
            lines = content.split("\n")
            insert_idx = 0
            for i, line in enumerate(lines):
                if line.startswith("# "):
                    insert_idx = i + 1
                    # Skip any blank lines after the heading
                    while insert_idx < len(lines) and not lines[insert_idx].strip():
                        insert_idx += 1
                    break
            lines.insert(insert_idx, entry)
            lines.insert(insert_idx + 1, "")
            _atomic_write(index_path, "\n".join(lines))
        else:
            # Already referenced — move it to the top if it's past line 200
            lines = content.split("\n")
            old_idx = None
            for i, line in enumerate(lines):
                if "user_archetype.md" in line:
                    old_idx = i
                    break
            if old_idx is not None and old_idx >= 200:
                lines.pop(old_idx)
                # Remove trailing blank line if it was there
                if old_idx < len(lines) and not lines[old_idx].strip():
                    lines.pop(old_idx)
                # Insert after first heading
                insert_idx = 0
                for i, line in enumerate(lines):
                    if line.startswith("# "):
                        insert_idx = i + 1
                        while insert_idx < len(lines) and not lines[insert_idx].strip():
                            insert_idx += 1
                        break
                lines.insert(insert_idx, entry)
                lines.insert(insert_idx + 1, "")
                _atomic_write(index_path, "\n".join(lines))

    return True


def write_cursor(archetype: str, project_root: Path) -> bool:
    """Write archetype to Cursor rules with MDC frontmatter."""
    rules_dir = project_root / ".cursor" / "rules"
    rules_dir.mkdir(parents=True, exist_ok=True)

    frontmatter = (
        "---\n"
        'description: "User archetype — coding preferences, behavioral guards, '
        'and known AI weaknesses extracted from session history"\n'
        "alwaysApply: true\n"
        "---\n\n"
    )
    path = rules_dir / "synapptic.mdc"
    _atomic_write(path, frontmatter + archetype)
    return True


def write_copilot(archetype: str, project_root: Path) -> bool:
    """Write archetype to GitHub Copilot instructions."""
    github_dir = project_root / ".github"
    github_dir.mkdir(parents=True, exist_ok=True)

    path = github_dir / "copilot-instructions.md"

    # If the file exists and has non-synapptic content, append rather than overwrite
    marker_start = "<!-- synapptic:start -->"
    marker_end = "<!-- synapptic:end -->"
    section = f"{marker_start}\n{archetype}\n{marker_end}"

    if path.exists():
        content = path.read_text()
        if marker_start in content:
            # Replace existing synapptic section
            import re
            content = re.sub(
                f"{re.escape(marker_start)}.*?{re.escape(marker_end)}",
                lambda m: section,
                content,
                flags=re.DOTALL,
            )
            _atomic_write(path, content)
        else:
            # Append
            if not content.endswith("\n"):
                content += "\n"
            content += f"\n{section}\n"
            _atomic_write(path, content)
    else:
        _atomic_write(path, section + "\n")

    return True


def write_gemini(archetype: str, project_root: Path) -> bool:
    """Write archetype to .gemini/styleguide.md."""
    gemini_dir = project_root / ".gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    path = gemini_dir / "styleguide.md"

    marker_start = "<!-- synapptic:start -->"
    marker_end = "<!-- synapptic:end -->"
    section = f"{marker_start}\n{archetype}\n{marker_end}"

    if path.exists():
        content = path.read_text()
        if marker_start in content:
            import re
            content = re.sub(
                f"{re.escape(marker_start)}.*?{re.escape(marker_end)}",
                lambda m: section,
                content,
                flags=re.DOTALL,
            )
            _atomic_write(path, content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"\n{section}\n"
            _atomic_write(path, content)
    else:
        _atomic_write(path, section + "\n")

    return True


def write_codex(archetype: str, project_root: Path) -> bool:
    """Write archetype to OpenAI Codex CLI AGENTS.md."""
    path = project_root / "AGENTS.md"
    marker_start = "<!-- synapptic:start -->"
    marker_end = "<!-- synapptic:end -->"
    section = f"{marker_start}\n{archetype}\n{marker_end}"

    if path.exists():
        content = path.read_text()
        if marker_start in content:
            import re
            content = re.sub(
                f"{re.escape(marker_start)}.*?{re.escape(marker_end)}",
                lambda m: section, content, flags=re.DOTALL,
            )
            _atomic_write(path, content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"\n{section}\n"
            _atomic_write(path, content)
    else:
        _atomic_write(path, section + "\n")
    return True


def write_windsurf(archetype: str, project_root: Path) -> bool:
    """Write archetype to .windsurfrules."""
    path = project_root / ".windsurfrules"
    _atomic_write(path, archetype + "\n")
    return True


def write_cline(archetype: str, project_root: Path) -> bool:
    """Write archetype to .clinerules."""
    path = project_root / ".clinerules"
    _atomic_write(path, archetype + "\n")
    return True


def write_aider(archetype: str, project_root: Path) -> bool:
    """Write archetype to CONVENTIONS.md."""
    path = project_root / "CONVENTIONS.md"
    _atomic_write(path, archetype + "\n")
    return True


def write_continue(archetype: str, project_root: Path) -> bool:
    """Write archetype to .continuerules."""
    path = project_root / ".continuerules"
    _atomic_write(path, archetype + "\n")
    return True


def resolve_target_model(target: str, profile: dict) -> str | None:
    """Find the best model to filter guards for a given output target.

    Looks at model_verdicts keys in the profile and matches by model_family prefix.
    Returns the most-tested model name, or None if no match.
    """
    family = OUTPUTS.get(target, {}).get("model_family")
    if not family:
        return None

    # Collect all model names from model_verdicts across guards
    model_counts = {}
    for dim in ("guards", "ai_failures"):
        for pref in profile.get("dimensions", {}).get(dim, []):
            for model_name in pref.get("model_verdicts", {}):
                if model_name.lower().startswith(family) or family in model_name.lower():
                    model_counts[model_name] = model_counts.get(model_name, 0) + 1

    if not model_counts:
        return None

    # Return the model with the most verdicts
    return max(model_counts, key=model_counts.get)


WRITERS = {
    "claude-code": write_claude_code,
    "cursor": write_cursor,
    "copilot": write_copilot,
    "gemini": write_gemini,
    "codex": write_codex,
    "windsurf": write_windsurf,
    "cline": write_cline,
    "aider": write_aider,
    "continue": write_continue,
}
