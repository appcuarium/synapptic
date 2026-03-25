"""Tests for synapptic.state module."""

import json

import pytest

from synapptic.state import (
    list_projects,
    load_archetype,
    load_observations,
    load_profile,
    save_archetype,
    save_observations,
    save_profile,
    slug_from_project_dir,
)


@pytest.fixture
def redirected(tmp_path, monkeypatch):
    """Redirect all state directories to tmp_path so tests never touch real state."""
    base = tmp_path / ".synapptic"
    globaldir = base / "global"
    projectsdir = base / "projects"
    historydir = base / "profile_history"

    monkeypatch.setattr("synapptic.state.SYNAPPTIC_DIR", base)
    monkeypatch.setattr("synapptic.state.GLOBAL_DIR", globaldir)
    monkeypatch.setattr("synapptic.state.PROJECTS_DIR", projectsdir)
    monkeypatch.setattr("synapptic.state.PROFILE_HISTORY_DIR", historydir)

    return {
        "base": base,
        "globaldir": globaldir,
        "projectsdir": projectsdir,
        "historydir": historydir,
    }


class TestProfileRoundtrip:

    def test_global_profile_roundtrip(self, redirected):
        profile = {
            "dimensions": {"workflow": [{"observation": "reads files first", "weight": 0.9}]},
            "metadata": {"total_sessions_analyzed": 3, "last_updated": "2026-03-20", "profile_version": 1},
        }
        save_profile(profile)
        loaded = load_profile()
        assert loaded == profile

    def test_project_profile_roundtrip(self, redirected):
        profile = {
            "dimensions": {"code_style": [{"observation": "no docstrings", "weight": 0.85}]},
            "metadata": {"total_sessions_analyzed": 1, "last_updated": "2026-03-21", "profile_version": 2},
        }
        save_profile(profile, project_slug="my-app")
        loaded = load_profile(project_slug="my-app")
        assert loaded == profile


class TestArchetypeRoundtrip:

    def test_global_archetype_roundtrip(self, redirected):
        text = "A senior engineer who values minimal changes."
        save_archetype(text)
        loaded = load_archetype()
        assert loaded == text

    def test_project_archetype_roundtrip(self, redirected):
        text = "Strict import ordering on this project."
        save_archetype(text, project_slug="trading-bot")
        loaded = load_archetype(project_slug="trading-bot")
        assert loaded == text

    def test_load_archetype_missing_returns_none(self, redirected):
        assert load_archetype() is None


class TestObservationsRoundtrip:

    def test_global_observations_roundtrip(self, redirected, sample_observations):
        save_observations("sess-001", sample_observations)
        loaded = load_observations("sess-001")
        assert loaded["observations"] == sample_observations
        assert loaded["session_id"] == "sess-001"

    def test_project_observations_roundtrip(self, redirected, sample_observations):
        save_observations("sess-002", sample_observations, project_slug="my-app")
        loaded = load_observations("sess-002", project_slug="my-app")
        assert loaded["observations"] == sample_observations

    def test_load_observations_missing_returns_none(self, redirected):
        assert load_observations("nonexistent") is None


class TestLoadProfileNonExistent:

    def test_returns_empty_profile(self, redirected):
        result = load_profile(project_slug="does-not-exist")
        assert result["dimensions"] == {}
        assert result["metadata"]["total_sessions_analyzed"] == 0

    def test_global_missing_returns_empty(self, redirected):
        result = load_profile()
        assert result["dimensions"] == {}


class TestProfileHistory:

    def test_history_file_created_for_global(self, redirected):
        profile = {
            "dimensions": {},
            "metadata": {"total_sessions_analyzed": 1, "last_updated": "2026-03-20", "profile_version": 3},
        }
        save_profile(profile)
        history = list(redirected["historydir"].glob("global_*_v3.yaml"))
        assert len(history) == 1

    def test_history_file_created_for_project(self, redirected):
        profile = {
            "dimensions": {},
            "metadata": {"total_sessions_analyzed": 2, "last_updated": "2026-03-21", "profile_version": 7},
        }
        save_profile(profile, project_slug="my-app")
        history = list(redirected["historydir"].glob("my-app_*_v7.yaml"))
        assert len(history) == 1


class TestAtomicWrite:

    def test_no_tmp_after_global_save(self, redirected):
        save_profile({"dimensions": {}, "metadata": {"total_sessions_analyzed": 0, "last_updated": None, "profile_version": 0}})
        assert list(redirected["globaldir"].glob("*.tmp")) == []

    def test_no_tmp_after_archetype_save(self, redirected):
        save_archetype("test", project_slug="my-app")
        projectdir = redirected["projectsdir"] / "my-app"
        assert list(projectdir.glob("*.tmp")) == []

    def test_no_tmp_left_on_write_exception(self, redirected):
        """If an exception occurs during the write, the .tmp file must be cleaned up."""
        import unittest.mock as mock
        import os

        profile = {"dimensions": {}, "metadata": {"total_sessions_analyzed": 0, "last_updated": None, "profile_version": 0}}
        with mock.patch("os.fsync", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                save_profile(profile)
        assert list(redirected["globaldir"].glob("*.tmp")) == []


class TestSlugFromProjectDir:

    def test_simple_path(self):
        result = slug_from_project_dir("-home-user-projects-my-app")
        assert "my-app" in result
        assert result  # not empty

    def test_deep_path(self):
        result = slug_from_project_dir("-Users-dev-src-code-repos-trading-bot")
        assert "trading-bot" in result

    def test_deterministic(self):
        a = slug_from_project_dir("-Users-sorin-Work-machine-be")
        b = slug_from_project_dir("-Users-sorin-Work-machine-be")
        assert a == b


class TestListProjects:

    def test_returns_slugs_with_profiles(self, redirected):
        projectsdir = redirected["projectsdir"]
        for slug in ["alpha", "beta"]:
            proj = projectsdir / slug
            proj.mkdir(parents=True)
            (proj / "profile.yaml").write_text("dimensions: {}")
        # Bare directory without profile — excluded
        bare = projectsdir / "bare"
        bare.mkdir(parents=True)
        result = list_projects()
        assert result == ["alpha", "beta"]

    def test_empty_when_no_projects(self, redirected):
        assert list_projects() == []
