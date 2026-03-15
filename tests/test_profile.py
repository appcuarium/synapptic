"""Tests for profile merging and accumulation."""

from synapptic.profile import merge_observations, profile_summary, find_match


def test_merge_into_empty_profile(empty_profile, sample_observations):
    """Merging observations into an empty profile should create new preferences."""
    result = merge_observations(empty_profile, sample_observations)

    dims = result["dimensions"]
    assert "workflow" in dims
    assert "communication" in dims
    assert "code_style" in dims

    # Should have the right counts
    assert len(dims["workflow"]) == 2  # read-first + no-auto-commit
    assert len(dims["communication"]) == 1
    assert len(dims["code_style"]) == 2  # imports + docstrings


def test_merge_reinforces_existing(populated_profile, sample_observations):
    """Merging similar observations should reinforce existing preferences."""
    result = merge_observations(populated_profile, sample_observations)

    workflow_prefs = result["dimensions"]["workflow"]
    # The "reads files before modifying" preference should be reinforced
    read_first = next(p for p in workflow_prefs if "read" in p["observation"].lower())
    assert read_first["evidence_count"] > 5  # Was 5, should increase


def test_decay_applied(populated_profile):
    """All existing preferences should be decayed even without new observations."""
    original_weight = populated_profile["dimensions"]["workflow"][0]["weight"]
    result = merge_observations(populated_profile, [], decay_factor=0.9)

    new_weight = result["dimensions"]["workflow"][0]["weight"]
    assert new_weight < original_weight
    assert abs(new_weight - original_weight * 0.9) < 0.01


def test_low_weight_archived(populated_profile):
    """Preferences below MIN_WEIGHT_THRESHOLD should be removed."""
    # Set a preference to very low weight
    populated_profile["dimensions"]["communication"][0]["weight"] = 0.05

    result = merge_observations(populated_profile, [], decay_factor=0.5)

    # The low-weight communication preference should be archived
    comm = result["dimensions"].get("communication", [])
    assert len(comm) == 0  # weight 0.05 * 0.5 = 0.025, below threshold


def test_metadata_updated(empty_profile, sample_observations):
    """Metadata should be updated after merge."""
    result = merge_observations(empty_profile, sample_observations)

    meta = result["metadata"]
    assert meta["total_sessions_analyzed"] == 1  # All from session-001
    assert meta["profile_version"] == 1
    assert meta["last_updated"] is not None


def test_find_match_similar():
    """Similar observations should match."""
    prefs = [
        {"observation": "User always reads files before modifying them"},
        {"observation": "User prefers terse responses"},
    ]
    idx = find_match(prefs, "User always reads files before modifying code")
    assert idx == 0


def test_find_match_no_match():
    """Dissimilar observations should not match."""
    prefs = [
        {"observation": "User always reads files before modifying them"},
    ]
    idx = find_match(prefs, "User likes Python type annotations")
    assert idx is None


def test_profile_summary(populated_profile):
    """profile_summary should produce readable output."""
    summary = profile_summary(populated_profile)
    assert "Workflow" in summary
    assert "Communication" in summary
    assert "v5" in summary


def test_sorted_by_weight(empty_profile):
    """After merge, preferences should be sorted by weight descending."""
    observations = [
        {
            "dimension": "workflow",
            "observation": "Prefers tabs over spaces for indentation",
            "confidence": 0.3,
            "session_id": "s1",
            "timestamp": "2026-03-15T00:00:00Z",
        },
        {
            "dimension": "workflow",
            "observation": "Always reads files before making any code modifications",
            "confidence": 0.95,
            "session_id": "s1",
            "timestamp": "2026-03-15T00:00:00Z",
        },
    ]
    result = merge_observations(empty_profile, observations)
    workflow = result["dimensions"]["workflow"]
    assert workflow[0]["weight"] > workflow[1]["weight"]
