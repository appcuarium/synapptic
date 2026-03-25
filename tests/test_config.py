"""Tests for synapptic.config module constants and path definitions."""

from pathlib import Path

from synapptic.config import (
    CHARS_PER_TOKEN,
    CLAUDE_PROJECTS_DIR,
    CORRECTION_SIGNALS,
    DEFAULT_DECAY_FACTOR,
    DEFAULT_MAX_TOKENS,
    DIMENSION_DESCRIPTIONS,
    DIMENSIONS,
    GLOBAL_DIMENSIONS,
    GLOBAL_PROMOTION_MIN_PROJECTS,
    MIN_WEIGHT_THRESHOLD,
    MIXED_DIMENSIONS,
    PROJECT_DIMENSIONS,
    SYNAPPTIC_DIR,
    TIME_DECAY_HALFLIFE_DAYS,
)


class TestPaths:

    def test_synapptic_dir_under_home(self):
        home = Path.home()
        assert SYNAPPTIC_DIR.is_relative_to(home)

    def test_claude_projects_dir_under_home(self):
        home = Path.home()
        assert CLAUDE_PROJECTS_DIR.is_relative_to(home)


class TestDimensions:

    def test_all_dimensions_defined(self):
        assert len(DIMENSIONS) == 9
        assert "guards" in DIMENSIONS
        assert "ai_failures" in DIMENSIONS
        assert "workflow" in DIMENSIONS

    def test_every_dimension_has_description(self):
        for dim in DIMENSIONS:
            assert dim in DIMENSION_DESCRIPTIONS, f"Missing description for dimension: {dim}"
            assert len(DIMENSION_DESCRIPTIONS[dim]) > 10

    def test_dimension_sets_are_disjoint(self):
        overlap = GLOBAL_DIMENSIONS & PROJECT_DIMENSIONS
        assert not overlap, f"Dimensions in both global and project: {overlap}"

    def test_all_routed_dimensions_are_known(self):
        all_routed = GLOBAL_DIMENSIONS | PROJECT_DIMENSIONS | MIXED_DIMENSIONS
        for dim in all_routed:
            assert dim in DIMENSIONS, f"Routed dimension '{dim}' not in DIMENSIONS list"


class TestDefaults:

    def test_decay_factor_range(self):
        assert 0 < DEFAULT_DECAY_FACTOR <= 1.0

    def test_min_weight_threshold_positive(self):
        assert MIN_WEIGHT_THRESHOLD > 0

    def test_chars_per_token_positive(self):
        assert CHARS_PER_TOKEN > 0

    def test_default_max_tokens_positive(self):
        assert DEFAULT_MAX_TOKENS > 0

    def test_time_decay_halflife_positive(self):
        assert TIME_DECAY_HALFLIFE_DAYS > 0

    def test_global_promotion_min_projects_at_least_two(self):
        assert GLOBAL_PROMOTION_MIN_PROJECTS >= 2


class TestSignals:

    def test_correction_signals_nonempty(self):
        assert len(CORRECTION_SIGNALS) > 0

    def test_correction_signals_are_strings(self):
        assert all(isinstance(s, str) for s in CORRECTION_SIGNALS)
