"""Tests for profile merging and accumulation."""

from datetime import datetime, timezone

from synapptic.profile import merge_observations, profile_summary, find_match, promote_to_global


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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


def test_stable_key_no_collision_for_long_shared_prefix(empty_profile):
    """Two observations sharing the same first 120+ chars must not collide on pre-decay weight lookup."""
    prefix = "A" * 110
    obs1 = prefix + " do X"
    obs2 = prefix + " do Y"
    today = _today()
    profile = {
        "dimensions": {
            "guards": [
                {"observation": obs1, "weight": 0.9, "evidence_count": 3,
                 "first_seen": today, "last_seen": today, "sources": []},
                {"observation": obs2, "weight": 0.2, "evidence_count": 1,
                 "first_seen": today, "last_seen": today, "sources": []},
            ]
        },
        "metadata": {"total_sessions_analyzed": 0, "last_updated": None, "profile_version": 0},
    }
    # Decay without new observations — both entries should survive as separate items
    # and their weights should differ (not merged or swapped due to key collision)
    result = merge_observations(profile, [], decay_factor=0.9)
    guards = result["dimensions"].get("guards", [])
    assert len(guards) == 2, "both preferences must survive as separate entries"
    w = {p["observation"]: p["weight"] for p in guards}
    assert w[obs1] > w[obs2], "high-weight pref must remain higher than low-weight after decay"


class TestPromoteToGlobal:

    def test_appears_in_two_projects_promotes(self):
        """An observation in a MIXED_DIMENSION appearing in 2+ projects should be returned for promotion."""
        projects = {
            "project-a": {
                "dimensions": {
                    "guards": [
                        {"observation": "NEVER commit without running tests",
                         "weight": 0.9, "evidence_count": 3, "sources": [], "projects": ["project-a"]}
                    ]
                }
            },
            "project-b": {
                "dimensions": {
                    "guards": [
                        {"observation": "NEVER commit without running tests first",
                         "weight": 0.85, "evidence_count": 2, "sources": [], "projects": ["project-b"]}
                    ]
                }
            },
        }
        global_profile = {"dimensions": {}, "metadata": {}}
        promotions = promote_to_global(projects, global_profile)
        assert len(promotions) == 1
        assert "commit" in promotions[0]["observation"].lower()

    def test_single_project_does_not_promote(self):
        """An observation in only one project should NOT be promoted to global."""
        projects = {
            "project-a": {
                "dimensions": {
                    "guards": [
                        {"observation": "NEVER use star imports",
                         "weight": 0.9, "evidence_count": 2, "sources": [], "projects": ["project-a"]}
                    ]
                }
            }
        }
        global_profile = {"dimensions": {}, "metadata": {}}
        promotions = promote_to_global(projects, global_profile)
        assert len(promotions) == 0

    def test_already_in_global_not_duplicated(self):
        """An observation already in the global profile should not be returned again."""
        obs = "NEVER commit without running tests"
        projects = {
            "project-a": {"dimensions": {"guards": [{"observation": obs, "weight": 0.9, "evidence_count": 2, "sources": []}]}},
            "project-b": {"dimensions": {"guards": [{"observation": obs, "weight": 0.85, "evidence_count": 2, "sources": []}]}},
        }
        global_profile = {
            "dimensions": {"guards": [{"observation": obs, "weight": 0.8, "evidence_count": 1, "sources": []}]}
        }
        promotions = promote_to_global(projects, global_profile)
        assert len(promotions) == 0

    def test_project_dimension_never_promotes(self):
        """Observations in PROJECT_DIMENSIONS (code_style) should not be promoted."""
        projects = {
            "project-a": {"dimensions": {"code_style": [{"observation": "use 2-space indent", "weight": 0.9, "evidence_count": 2, "sources": []}]}},
            "project-b": {"dimensions": {"code_style": [{"observation": "use 2-space indent", "weight": 0.9, "evidence_count": 2, "sources": []}]}},
        }
        global_profile = {"dimensions": {}, "metadata": {}}
        promotions = promote_to_global(projects, global_profile)
        assert len(promotions) == 0
