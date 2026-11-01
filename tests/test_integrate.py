"""Tests for synapptic.integrate — archetype combination, path resolution."""

from pathlib import Path

from synapptic.integrate import (
    _apply_target_filter,
    _combine_profiles,
    _removed_rules_for_target,
    combine_archetypes,
    resolve_project_root,
)


class TestCombineArchetypes:

    def test_global_only(self):
        result = combine_archetypes("Global content", None, "my-project")
        assert "Global content" in result
        assert "Project-specific" not in result

    def test_project_only(self):
        result = combine_archetypes("", "Project content", "my-project")
        assert "Project content" in result

    def test_both(self):
        result = combine_archetypes("Global", "Project", "my-project")
        assert "Global" in result
        assert "Project" in result
        assert "my-project" in result

    def test_empty_both(self):
        result = combine_archetypes("", None, "my-project")
        assert result.strip() == ""


class TestResolveProjectRoot:

    def test_nonexistent_returns_none(self):
        result = resolve_project_root("-nonexistent-fake-path-xyz")
        assert result is None

    def test_rejects_outside_home(self, tmp_path, monkeypatch):
        # Mock Path.home() to a temp directory
        fake_home = tmp_path / "fakehome"
        fake_home.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
        # /etc exists but is outside fake_home
        result = resolve_project_root("-etc")
        assert result is None

    def test_rejects_sibling_with_matching_prefix(self, tmp_path, monkeypatch):
        # Path.home() = tmp_path/alice; sibling tmp_path/alice_evil must be rejected.
        # startswith() would pass "/tmp/alice_evil".startswith("/tmp/alice") — relative_to() does not.
        home = tmp_path / "alice"
        home.mkdir()
        sibling = tmp_path / "alice_evil"
        sibling.mkdir()
        monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
        encoded = str(sibling).lstrip("/").replace("_", "--").replace("/", "-")
        result = resolve_project_root(encoded)
        assert result is None

    def test_valid_path_under_home(self):
        # Use the actual home directory to test — it exists and is under home
        home = Path.home()
        # Encode the home path itself
        encoded = str(home).lstrip("/").replace("_", "--").replace("/", "-")
        result = resolve_project_root(encoded)
        assert result is not None
        assert result == home.resolve()


class TestModelFilteringInIntegrate:

    def test_removed_rules_for_target(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "NEVER summarize at the end", "weight": 0.9, "model_verdicts": {"gemini": "redundant"}},
                    {"observation": "ALWAYS read files first", "weight": 0.9, "model_verdicts": {"gemini": "effective"}},
                ]
            }
        }
        removed = _removed_rules_for_target(profile, "gemini")
        assert "NEVER summarize at the end" in removed
        assert "ALWAYS read files first" not in removed

    def test_apply_target_filter_removes_marked_guard_line(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "NEVER summarize at the end", "weight": 0.9, "model_verdicts": {"gemini": "redundant"}},
                    {"observation": "ALWAYS read files first", "weight": 0.9, "model_verdicts": {"gemini": "effective"}},
                ]
            }
        }
        archetype = """## Guards
1. NEVER summarize at the end
2. ALWAYS read files first
"""
        filtered, excluded = _apply_target_filter(archetype, profile, "gemini")
        assert excluded == 1
        assert "NEVER summarize at the end" not in filtered
        assert "ALWAYS read files first" in filtered

    def test_combine_profiles_includes_global_and_project_dims(self):
        gp = {"dimensions": {"guards": [{"observation": "G1"}]}}
        pp = {"dimensions": {"guards": [{"observation": "G2"}], "workflow": [{"observation": "W1"}]}}
        combined = _combine_profiles(gp, pp)
        guard_texts = [x["observation"] for x in combined["dimensions"]["guards"]]
        assert "G1" in guard_texts
        assert "G2" in guard_texts
        assert "workflow" in combined["dimensions"]

