"""Test fixtures for synapptic."""

from datetime import datetime, timezone
from pathlib import Path

import pytest


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture
def sample_session_path():
    return FIXTURES_DIR / "sample_session.jsonl"


@pytest.fixture
def sample_observations():
    return [
        {
            "dimension": "workflow",
            "observation": "User always reads files before modifying them",
            "confidence": 0.95,
            "evidence": "User said 'Read the file first before making changes'",
            "session_id": "session-001",
            "timestamp": "2026-03-15T10:00:00Z",
        },
        {
            "dimension": "communication",
            "observation": "User dislikes verbose summaries after changes",
            "confidence": 0.9,
            "evidence": "User said 'stop summarizing what you did. I can see the diff.'",
            "session_id": "session-001",
            "timestamp": "2026-03-15T10:00:00Z",
        },
        {
            "dimension": "code_style",
            "observation": "All imports must be at file top, never inside functions",
            "confidence": 0.95,
            "evidence": "User said 'put imports at the top of the file. Never inside functions.'",
            "session_id": "session-001",
            "timestamp": "2026-03-15T10:00:00Z",
        },
        {
            "dimension": "code_style",
            "observation": "User hates unnecessary docstrings and comments",
            "confidence": 0.85,
            "evidence": "User said 'don't add any docstrings — I hate unnecessary comments'",
            "session_id": "session-001",
            "timestamp": "2026-03-15T10:00:00Z",
        },
        {
            "dimension": "workflow",
            "observation": "User wants to test before committing, don't auto-commit",
            "confidence": 0.9,
            "evidence": "User said 'don't commit yet, let me test first'",
            "session_id": "session-001",
            "timestamp": "2026-03-15T10:00:00Z",
        },
    ]


@pytest.fixture
def empty_profile():
    return {
        "dimensions": {},
        "metadata": {
            "total_sessions_analyzed": 0,
            "last_updated": None,
            "profile_version": 0,
        },
    }


@pytest.fixture
def populated_profile():
    return {
        "dimensions": {
            "workflow": [
                {
                    "observation": "User always reads files before modifying",
                    "weight": 0.85,
                    "evidence_count": 5,
                    "first_seen": "2026-01-01T00:00:00Z",
                    "last_seen": _today(),
                    "sources": ["session-old-1", "session-old-2"],
                },
            ],
            "communication": [
                {
                    "observation": "User prefers terse responses without summaries",
                    "weight": 0.70,
                    "evidence_count": 3,
                    "first_seen": "2026-02-01T00:00:00Z",
                    "last_seen": _today(),
                    "sources": ["session-old-3"],
                },
            ],
        },
        "metadata": {
            "total_sessions_analyzed": 10,
            "last_updated": "2026-03-10T00:00:00Z",
            "profile_version": 5,
        },
    }
