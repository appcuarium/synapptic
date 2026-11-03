"""Paths, defaults, and dimension definitions for synaptic."""

from pathlib import Path


# State directory — all synaptic data lives here
SYNAPPTIC_DIR = Path.home() / ".synapptic"
GLOBAL_DIR = SYNAPPTIC_DIR / "global"
PROJECTS_DIR = SYNAPPTIC_DIR / "projects"
PROFILE_HISTORY_DIR = SYNAPPTIC_DIR / "profile_history"
BENCHMARKS_DIR = SYNAPPTIC_DIR / "benchmarks"
CONFIG_PATH = SYNAPPTIC_DIR / "config.yaml"
QUEUE_PATH = SYNAPPTIC_DIR / "queue.txt"
CURSORS_DIR = SYNAPPTIC_DIR / "cursors"  # per session: byte offset of the transcript already extracted
PIPELINE_LOCK_PATH = SYNAPPTIC_DIR / "pipeline.lock"  # one extraction at a time (hooks, checkpoint, ingest)
BATCHES_DIR = SYNAPPTIC_DIR / "batches"  # submitted Message Batches awaiting collection, plus chunks to retry

# Checkpoint defaults (extraction of a live session in chunks)
CHECKPOINT_MIN_TOKENS = 30_000  # a Stop-hook checkpoint waits until this much new conversation exists
SYNTHESIZE_MIN_HOURS = 6  # a checkpoint re-synthesizes an archetype at most this often (a flush always does)

# Claude Code paths
CLAUDE_DIR = Path.home() / ".claude"
CLAUDE_PROJECTS_DIR = CLAUDE_DIR / "projects"

# Filtering defaults
DEFAULT_MAX_TOKENS = 50_000
CHARS_PER_TOKEN = 4  # rough approximation

# Observation dimensions (how they weigh, decay, route and promote is the core's)
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
