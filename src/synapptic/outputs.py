"""Multi-platform output writers for synapptic.

Writes the archetype to different AI coding assistant formats:
- Claude Code: ~/.claude/projects/*/memory/user_archetype.md (with YAML frontmatter)
- Cursor: .cursorrules or .cursor/rules/synapptic.mdc (with frontmatter)
- GitHub Copilot: .github/copilot-instructions.md
- Gemini: GEMINI.md
"""

from pathlib import Path


# Available output targets
OUTPUTS = {
    "claude-code": {
        "name": "Claude Code",
        "description": "Writes to Claude Code memory system (~/.claude/projects/*/memory/)",
    },
    "cursor": {
        "name": "Cursor",
        "description": "Writes to .cursor/rules/synapptic.mdc (project root)",
    },
    "copilot": {
        "name": "GitHub Copilot",
        "description": "Writes to .github/copilot-instructions.md (project root)",
    },
    "gemini": {
        "name": "Gemini",
        "description": "Writes to GEMINI.md (project root)",
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
    path.write_text(frontmatter + archetype)

    # Ensure MEMORY.md references it
    index_path = memory_dir / "MEMORY.md"
    if index_path.exists():
        content = index_path.read_text()
        if "user_archetype.md" not in content:
            entry = (
                "\n- [user_archetype.md](./user_archetype.md) "
                "— Auto-generated user archetype from session transcripts (synapptic)\n"
            )
            if not content.endswith("\n"):
                content += "\n"
            content += entry
            index_path.write_text(content)

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
    path.write_text(frontmatter + archetype)
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
                section,
                content,
                flags=re.DOTALL,
            )
            path.write_text(content)
        else:
            # Append
            if not content.endswith("\n"):
                content += "\n"
            content += f"\n{section}\n"
            path.write_text(content)
    else:
        path.write_text(section + "\n")

    return True


def write_gemini(archetype: str, project_root: Path) -> bool:
    """Write archetype to GEMINI.md."""
    path = project_root / "GEMINI.md"

    marker_start = "<!-- synapptic:start -->"
    marker_end = "<!-- synapptic:end -->"
    section = f"{marker_start}\n{archetype}\n{marker_end}"

    if path.exists():
        content = path.read_text()
        if marker_start in content:
            import re
            content = re.sub(
                f"{re.escape(marker_start)}.*?{re.escape(marker_end)}",
                section,
                content,
                flags=re.DOTALL,
            )
            path.write_text(content)
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += f"\n{section}\n"
            path.write_text(content)
    else:
        path.write_text(section + "\n")

    return True


WRITERS = {
    "claude-code": write_claude_code,
    "cursor": write_cursor,
    "copilot": write_copilot,
    "gemini": write_gemini,
}
