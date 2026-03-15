"""Paths, defaults, and dimension definitions for synaptic."""

from pathlib import Path


# State directory — all synaptic data lives here
SYNAPPTIC_DIR = Path.home() / ".synapptic"
GLOBAL_DIR = SYNAPPTIC_DIR / "global"
PROJECTS_DIR = SYNAPPTIC_DIR / "projects"
PROFILE_HISTORY_DIR = SYNAPPTIC_DIR / "profile_history"
CONFIG_PATH = SYNAPPTIC_DIR / "config.yaml"
QUEUE_PATH = SYNAPPTIC_DIR / "queue.txt"

# Claude Code paths
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"

# Filtering defaults
DEFAULT_MAX_TOKENS = 50_000
CHARS_PER_TOKEN = 4  # rough approximation

# Profile defaults
DEFAULT_DECAY_FACTOR = 0.98
MIN_WEIGHT_THRESHOLD = 0.1  # archive below this
NARRATIVE_MIN_WEIGHT = 0.3  # include in archetype above this
NARRATIVE_MIN_EVIDENCE = 1  # weight is the primary quality filter

# Global promotion: observations appearing in N+ projects promote to global
GLOBAL_PROMOTION_MIN_PROJECTS = 2

# Dimensions that are inherently global (user traits, not project-specific)
GLOBAL_DIMENSIONS = {"communication", "values", "workflow", "expertise"}
# Dimensions that stay project-local only
PROJECT_DIMENSIONS = {"code_style"}
# Dimensions that start project-local but promote to global when seen across 2+ projects
MIXED_DIMENSIONS = {"expectations", "triggers", "ai_failures", "guards"}

# Observation dimensions
DIMENSIONS = [
    "expertise",
    "communication",
    "workflow",
    "code_style",
    "expectations",
    "triggers",
    "values",
    "ai_failures",
    "guards",
]

# Profiling mode dimension sets
USER_DIMENSIONS = {"expertise", "communication", "workflow", "code_style", "values"}
AGENT_DIMENSIONS = {"ai_failures", "guards"}
# expectations and triggers are relevant to both modes

DIMENSION_DESCRIPTIONS = {
    "expertise": "What the user knows well, skill levels, learning areas, domain knowledge",
    "communication": "Style, tone, verbosity preference, response format expectations",
    "workflow": "How they work — read-first, verify patterns, commit habits, testing approach",
    "code_style": "Formatting, imports, naming, organization preferences",
    "expectations": "What they expect from Claude — autonomy level, explanation depth, proactivity",
    "triggers": "What causes frustration or satisfaction, pet peeves, positive reinforcement",
    "values": "What they prioritize — correctness, speed, minimal changes, safety, simplicity",
    "ai_failures": "Specific things Claude did wrong in this session — mistakes, bad assumptions, wrong approaches, wasted effort, ignored instructions. Focus on PATTERNS of failure, not one-off typos.",
    "guards": "Concrete rules Claude should follow to avoid repeating observed failures. Phrased as imperatives: 'ALWAYS do X', 'NEVER do Y', 'BEFORE doing X, first do Y'. Each guard must trace back to a specific failure or correction.",
}

# Heuristic keywords for boosting interesting turns during filtering
CORRECTION_SIGNALS = [
    "no,", "no ", "not that", "instead", "i said", "don't", "dont", "wrong",
    "stop", "undo", "revert", "that's not", "thats not", "I meant",
    "why did you", "I didn't ask", "I didn't say",
]

PREFERENCE_SIGNALS = [
    "I prefer", "always", "never", "convention", "rule", "standard",
    "we use", "our pattern", "the way we", "important:", "remember",
    "from now on",
]

STRONG_REACTION_SIGNALS = [
    "!", "?!", "WTF", "wtf", "damn", "shit", "fuck", "NO", "STOP",
    "WRONG", "WHY", "ALWAYS", "NEVER",
]
