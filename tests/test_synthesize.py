"""Tests for synthesize.py — narrative archetype generation and profile filtering."""

from unittest.mock import patch

from synapptic.synthesize import filter_for_narrative, synthesize_archetype


class TestFilterForNarrative:

    def test_weight_threshold_filters_low(self):
        profile = {
            "dimensions": {
                "workflow": [
                    {"observation": "reads files first", "weight": 0.8, "evidence_count": 3},
                    {"observation": "low weight item", "weight": 0.1, "evidence_count": 1},
                ],
            },
        }
        result = filter_for_narrative(profile)
        assert len(result["dimensions"]["workflow"]) == 1
        assert result["dimensions"]["workflow"][0]["observation"] == "reads files first"

    def test_guards_use_lower_evidence_threshold(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "NEVER do X", "weight": 0.7, "evidence_count": 1},
                ],
            },
        }
        result = filter_for_narrative(profile)
        assert "guards" in result["dimensions"]

    def test_excluded_guards_filtered(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Active guard", "weight": 0.9},
                    {"observation": "Excluded guard", "weight": 0.9, "excluded": "backfire"},
                ],
            },
        }
        result = filter_for_narrative(profile)
        assert len(result["dimensions"]["guards"]) == 1
        assert result["dimensions"]["guards"][0]["observation"] == "Active guard"

    def test_model_verdicts_redundant_excluded(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Guard A", "weight": 0.9, "model_verdicts": {"gemini": "redundant"}},
                    {"observation": "Guard B", "weight": 0.9, "model_verdicts": {"gemini": "effective"}},
                ],
            },
        }
        result = filter_for_narrative(profile, target_model="gemini")
        texts = [g["observation"] for g in result["dimensions"]["guards"]]
        assert "Guard A" not in texts
        assert "Guard B" in texts

    def test_model_verdicts_backfire_excluded(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Bad guard", "weight": 0.9, "model_verdicts": {"llama": "backfire"}},
                ],
            },
        }
        result = filter_for_narrative(profile, target_model="llama")
        assert "guards" not in result["dimensions"]

    def test_no_target_model_includes_all(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Guard", "weight": 0.9, "model_verdicts": {"gemini": "redundant"}},
                ],
            },
        }
        result = filter_for_narrative(profile, target_model=None)
        assert len(result["dimensions"]["guards"]) == 1

    def test_empty_profile(self):
        result = filter_for_narrative({"dimensions": {}})
        assert result["dimensions"] == {}

    def test_no_dimensions_key(self):
        result = filter_for_narrative({})
        assert result["dimensions"] == {}


class TestSynthesizeArchetype:

    def test_calls_llm_with_profile(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "NEVER do X", "weight": 0.9, "evidence_count": 2},
                ],
            },
        }
        with patch("synapptic.synthesize.call_llm", return_value="## User Archetype\nTest narrative") as mock:
            result = synthesize_archetype(profile, config={"provider": "test"})
            assert result == "## User Archetype\nTest narrative"
            assert mock.called
            prompt = mock.call_args[0][0]
            assert "NEVER do X" in prompt

    def test_strips_markdown_fences(self):
        profile = {
            "dimensions": {
                "guards": [{"observation": "NEVER do X", "weight": 0.9, "evidence_count": 2}],
            },
        }
        with patch("synapptic.synthesize.call_llm", return_value="```markdown\n## Archetype\nContent\n```"):
            result = synthesize_archetype(profile, config={"provider": "test"})
            assert result.startswith("## Archetype")
            assert "```" not in result

    def test_empty_profile_returns_none(self, capsys):
        result = synthesize_archetype({"dimensions": {}}, config={"provider": "test"})
        assert result is None

    def test_llm_failure_returns_none(self):
        profile = {
            "dimensions": {
                "guards": [{"observation": "NEVER do X", "weight": 0.9, "evidence_count": 2}],
            },
        }
        with patch("synapptic.synthesize.call_llm", return_value=None):
            assert synthesize_archetype(profile, config={"provider": "test"}) is None

    def test_target_model_filters_guards(self):
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Effective guard", "weight": 0.9, "model_verdicts": {"gemini": "effective"}},
                    {"observation": "Redundant guard", "weight": 0.9, "model_verdicts": {"gemini": "redundant"}},
                ],
            },
        }
        with patch("synapptic.synthesize.call_llm", return_value="## Archetype\nFiltered") as mock:
            synthesize_archetype(profile, config={"provider": "test"}, target_model="gemini")
            prompt = mock.call_args[0][0]
            assert "Effective guard" in prompt
            assert "Redundant guard" not in prompt
