"""Tests for synapptic.integrate — archetype combination, path resolution."""

from pathlib import Path
from unittest.mock import patch

from synapptic.integrate import combine_archetypes, resolve_project_root


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
