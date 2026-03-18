"""Tests for benchmark guard selection, judging, fidelity, dedup, and guard removal."""

from unittest.mock import patch

from synapptic.benchmark import (
    _binomial_ci,
    _deduplicate_guards,
    _majority_vote,
    judge_response,
    remove_guard_from_archetype,
    score_response,
    select_guards,
    validate_test_fidelity,
)


# --- Fixtures ---

GUARDS = [
    "NEVER write a trailing summary after completing a change",
    "WHEN user says 'I cannot do X', treat it as a BUG REPORT",
    "NEVER announce 'let me write a plan' after reading files",
    "WHEN user says 'revert this shit', manually edit the file to undo changes",
    "NEVER auto-commit or auto-push — commits are explicit commands",
    "WHEN investigating a bug, ONLY fix what was reported",
    "NEVER provide specific menu paths unless certain they exist",
    "WHEN user says 'find by name', the lookup MUST use the specified field",
    "WHEN a financial computation produces wrong values after 2 failures, STOP",
    "NEVER use string interpolation on raw transcript content",
    "WHEN the user asks 'WHY is X different?', diagnose only — do NOT fix",
    "WHEN user says 'changes should be done inside [Function]', modify ONLY that function",
]

SAMPLE_ARCHETYPE = """# User Profile

## Guards
1. NEVER write a trailing summary after completing a change
2. WHEN user says 'I cannot do X', treat it as a BUG REPORT
3. NEVER announce 'let me write a plan' after reading files
4. WHEN investigating a bug, ONLY fix what was reported
5. NEVER auto-commit or auto-push — commits are explicit commands
"""


# --- select_guards determinism ---

class TestSelectGuards:

    def test_same_seed_same_selection(self):
        assert select_guards(GUARDS, 5, seed=42) == select_guards(GUARDS, 5, seed=42)

    def test_same_seed_100_runs(self):
        baseline = select_guards(GUARDS, 5, seed=99)
        for _ in range(100):
            assert select_guards(GUARDS, 5, seed=99) == baseline

    def test_different_seeds_different_selection(self):
        assert select_guards(GUARDS, 5, seed=42) != select_guards(GUARDS, 5, seed=43)

    def test_different_seeds_different_selection_many(self):
        selections = [tuple(select_guards(GUARDS, 5, seed=s)) for s in range(20)]
        assert len(set(selections)) > 1

    def test_n_greater_than_guards(self):
        result = select_guards(GUARDS, 100, seed=42)
        assert len(result) == len(GUARDS)
        assert set(result) == set(GUARDS)

    def test_n_equal_to_guards(self):
        result = select_guards(GUARDS, len(GUARDS), seed=42)
        assert len(result) == len(GUARDS)
        assert set(result) == set(GUARDS)

    def test_n_is_one(self):
        result = select_guards(GUARDS, 1, seed=42)
        assert len(result) == 1
        assert result[0] in GUARDS

    def test_n_is_zero(self):
        assert select_guards(GUARDS, 0, seed=42) == []

    def test_empty_guards(self):
        assert select_guards([], 5, seed=42) == []

    def test_selection_is_subset(self):
        for guard in select_guards(GUARDS, 5, seed=42):
            assert guard in GUARDS

    def test_no_duplicates(self):
        result = select_guards(GUARDS, 8, seed=42)
        assert len(result) == len(set(result))

    def test_seed_zero(self):
        result1 = select_guards(GUARDS, 5, seed=0)
        result2 = select_guards(GUARDS, 5, seed=0)
        assert result1 == result2
        assert len(result1) == 5

    def test_large_seed(self):
        assert select_guards(GUARDS, 5, seed=999999) == select_guards(GUARDS, 5, seed=999999)

    def test_order_matters(self):
        guards_reversed = list(reversed(GUARDS))
        assert select_guards(GUARDS, 5, seed=42) == select_guards(GUARDS, 5, seed=42)
        assert select_guards(guards_reversed, 5, seed=42) == select_guards(guards_reversed, 5, seed=42)

    def test_guard_list_not_mutated(self):
        guards_copy = list(GUARDS)
        select_guards(GUARDS, 5, seed=42)
        assert GUARDS == guards_copy


# --- judge_response ---

class TestJudgeResponse:

    def test_comply_verdict(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "COMPLY", "reason": "Response follows the rule."}'):
            result = judge_response("some response", "some rule", {})
            assert result["verdict"] == "COMPLY"
            assert "follows the rule" in result["reason"]

    def test_violate_verdict(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "VIOLATE", "reason": "Response breaks the rule."}'):
            assert judge_response("some response", "some rule", {})["verdict"] == "VIOLATE"

    def test_json_with_surrounding_text(self):
        with patch("synapptic.benchmark.call_llm", return_value='```json\n{"verdict": "VIOLATE", "reason": "bad"}\n```'):
            assert judge_response("some response", "some rule", {})["verdict"] == "VIOLATE"

    def test_plain_text_violate_fallback(self):
        with patch("synapptic.benchmark.call_llm", return_value="The response clearly VIOLATES the rule."):
            assert judge_response("some response", "some rule", {})["verdict"] == "VIOLATE"

    def test_plain_text_comply_fallback(self):
        with patch("synapptic.benchmark.call_llm", return_value="The response COMPLIES with the instruction."):
            assert judge_response("some response", "some rule", {})["verdict"] == "COMPLY"

    def test_llm_returns_none_is_unknown(self):
        with patch("synapptic.benchmark.call_llm", return_value=None):
            result = judge_response("some response", "some rule", {})
            assert result["verdict"] == "UNKNOWN"

    def test_no_json_no_keyword_is_unknown(self):
        with patch("synapptic.benchmark.call_llm", return_value="The response is interesting but hard to classify."):
            assert judge_response("some response", "some rule", {})["verdict"] == "UNKNOWN"

    def test_unrecognized_verdict_is_unknown(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "MAYBE", "reason": "unclear"}'):
            assert judge_response("some response", "some rule", {})["verdict"] == "UNKNOWN"

    def test_malformed_json_with_violate(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "VIOLATE", "reason": broken}'):
            assert judge_response("some response", "some rule", {})["verdict"] == "VIOLATE"

    def test_verdict_case_insensitive(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "comply", "reason": "ok"}'):
            assert judge_response("some response", "some rule", {})["verdict"] == "COMPLY"

    def test_verdict_violates_variant(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "VIOLATES", "reason": "it violates"}'):
            assert judge_response("some response", "some rule", {})["verdict"] == "VIOLATE"

    def test_score_response_comply_returns_pass(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "COMPLY", "reason": "ok"}'):
            result = score_response("some response", {"rule": "some rule"}, {})
            assert result["score"] == "PASS"
            assert result["reason"] == "ok"

    def test_score_response_violate_returns_fail(self):
        with patch("synapptic.benchmark.call_llm", return_value='{"verdict": "VIOLATE", "reason": "bad"}'):
            assert score_response("some response", {"rule": "some rule"}, {})["score"] == "FAIL"

    def test_score_response_unknown_on_failure(self):
        with patch("synapptic.benchmark.call_llm", return_value=None):
            assert score_response("some response", {"rule": "some rule"}, {})["score"] == "UNKNOWN"


# --- majority_vote ---

class TestMajorityVote:

    def test_clear_pass(self):
        assert _majority_vote(3, 3) == "PASS"
        assert _majority_vote(2, 3) == "PASS"

    def test_clear_fail(self):
        assert _majority_vote(0, 3) == "FAIL"
        assert _majority_vote(1, 3) == "FAIL"

    def test_tie_on_even_valid(self):
        """Even valid_runs with split votes → TIE, not silent FAIL."""
        assert _majority_vote(1, 2) == "TIE"
        assert _majority_vote(2, 4) == "TIE"

    def test_zero_valid(self):
        assert _majority_vote(0, 0) == "UNKNOWN"

    def test_single_run_pass(self):
        assert _majority_vote(1, 1) == "PASS"

    def test_single_run_fail(self):
        assert _majority_vote(0, 1) == "FAIL"


# --- validate_test_fidelity ---

class TestValidateTestFidelity:

    def test_exact_match_passes(self):
        tests = [{"rule": GUARDS[0], "scenario": "test"}]
        assert len(validate_test_fidelity(tests, GUARDS)) == 1

    def test_similar_match_passes(self):
        tests = [{"rule": "NEVER write trailing summaries after completing changes", "scenario": "test"}]
        assert len(validate_test_fidelity(tests, GUARDS)) == 1

    def test_unrelated_rule_dropped(self):
        tests = [{"rule": "Always use spaces instead of tabs", "scenario": "test"}]
        assert len(validate_test_fidelity(tests, GUARDS)) == 0

    def test_mixed_fidelity(self):
        tests = [
            {"rule": GUARDS[0], "scenario": "test1"},
            {"rule": "Completely invented guard about unicorns", "scenario": "test2"},
            {"rule": GUARDS[2], "scenario": "test3"},
        ]
        result = validate_test_fidelity(tests, GUARDS)
        assert len(result) == 2
        assert result[0]["rule"] == GUARDS[0]
        assert result[1]["rule"] == GUARDS[2]

    def test_empty_inputs(self):
        assert validate_test_fidelity([], GUARDS) == []
        assert validate_test_fidelity([{"rule": "x", "scenario": "y"}], []) == []


# --- deduplicate_guards ---

class TestDeduplicateGuards:

    def test_exact_duplicates_removed(self):
        guards = ["NEVER do X", "NEVER do X", "ALWAYS do Y"]
        # Exact dupes should be caught by the caller's set-based dedup,
        # but SequenceMatcher will also catch them
        result = _deduplicate_guards(guards)
        assert len(result) == 2

    def test_near_duplicates_removed(self):
        guards = [
            "NEVER auto-commit or auto-push",
            "NEVER automatically commit or push",
            "ALWAYS verify before deploying",
        ]
        result = _deduplicate_guards(guards)
        assert len(result) == 2
        assert result[0] == "NEVER auto-commit or auto-push"
        assert result[1] == "ALWAYS verify before deploying"

    def test_distinct_guards_kept(self):
        result = _deduplicate_guards(GUARDS)
        assert len(result) == len(GUARDS)

    def test_empty_list(self):
        assert _deduplicate_guards([]) == []

    def test_single_guard(self):
        assert _deduplicate_guards(["NEVER do X"]) == ["NEVER do X"]


# --- remove_guard_from_archetype ---

class TestRemoveGuardFromArchetype:

    def test_removes_exact_match(self):
        result = remove_guard_from_archetype(
            SAMPLE_ARCHETYPE, "NEVER write a trailing summary after completing a change")
        assert "trailing summary" not in result
        assert "BUG REPORT" in result
        assert "auto-commit" in result

    def test_removes_similar_match(self):
        result = remove_guard_from_archetype(
            SAMPLE_ARCHETYPE, "NEVER write a trailing summary after completing changes")
        assert "trailing summary" not in result

    def test_no_match_returns_full_archetype(self):
        result = remove_guard_from_archetype(SAMPLE_ARCHETYPE, "Always use tabs for indentation")
        assert result == SAMPLE_ARCHETYPE

    def test_removal_detectable_by_comparison(self):
        """Callers can detect failed removal by comparing result to original."""
        result = remove_guard_from_archetype(SAMPLE_ARCHETYPE, "nonexistent guard xyz")
        assert result == SAMPLE_ARCHETYPE  # no change = removal failed

        result2 = remove_guard_from_archetype(
            SAMPLE_ARCHETYPE, "NEVER auto-commit or auto-push — commits are explicit commands")
        assert result2 != SAMPLE_ARCHETYPE  # changed = removal succeeded

    def test_only_removes_one_line(self):
        result = remove_guard_from_archetype(
            SAMPLE_ARCHETYPE, "NEVER auto-commit or auto-push — commits are explicit commands")
        assert any("Guards" in line for line in result.split("\n"))
        assert "auto-commit" not in result

    def test_preserves_other_content(self):
        archetype = "# Profile\n\nSome context.\n\n- NEVER summarize\n- ALWAYS verify\n"
        result = remove_guard_from_archetype(archetype, "NEVER summarize")
        assert "Some context." in result
        assert "ALWAYS verify" in result

    def test_empty_archetype(self):
        assert remove_guard_from_archetype("", "some guard") == ""

    def test_threshold_respected(self):
        result = remove_guard_from_archetype(
            SAMPLE_ARCHETYPE, "xyz completely unrelated text abc", threshold=0.8)
        assert result == SAMPLE_ARCHETYPE

    def test_numbered_list_matching(self):
        archetype = "1. NEVER do X\n2. ALWAYS do Y\n3. WHEN Z, do W"
        result = remove_guard_from_archetype(archetype, "ALWAYS do Y")
        assert "ALWAYS do Y" not in result
        assert "NEVER do X" in result

    def test_bullet_list_matching(self):
        archetype = "- NEVER do X\n- ALWAYS do Y\n- WHEN Z, do W"
        result = remove_guard_from_archetype(archetype, "NEVER do X")
        assert "NEVER do X" not in result
        assert "ALWAYS do Y" in result

    def test_multiline_guard_removal(self):
        """Multi-line guards with continuation lines are fully removed."""
        archetype = (
            "- WHEN user reports a financial computation bug:\n"
            "  - Verify the calculation logic first\n"
            "  - STOP after 2 failures\n"
            "- NEVER auto-commit\n"
        )
        result = remove_guard_from_archetype(
            archetype, "WHEN user reports a financial computation bug")
        assert "financial computation" not in result
        assert "Verify the calculation" not in result
        assert "STOP after 2 failures" not in result
        assert "NEVER auto-commit" in result

    def test_multiline_stops_at_same_indent(self):
        """Continuation removal stops at lines with same or lesser indent."""
        archetype = (
            "- Guard one\n"
            "  - sub-item of guard one\n"
            "- Guard two\n"
            "  - sub-item of guard two\n"
        )
        result = remove_guard_from_archetype(archetype, "Guard one")
        assert "Guard one" not in result
        assert "sub-item of guard one" not in result
        assert "Guard two" in result
        assert "sub-item of guard two" in result


# --- binomial_ci ---

class TestBinomialCI:

    def test_zero_trials(self):
        assert _binomial_ci(0, 0) == (0.0, 0.0)

    def test_all_pass(self):
        lo, hi = _binomial_ci(10, 10)
        assert lo > 0.6
        assert hi == 1.0

    def test_all_fail(self):
        lo, hi = _binomial_ci(0, 10)
        assert lo == 0.0
        assert hi < 0.4

    def test_half(self):
        lo, hi = _binomial_ci(5, 10)
        assert lo < 0.5
        assert hi > 0.5

    def test_bounds_within_zero_one(self):
        for s in range(11):
            lo, hi = _binomial_ci(s, 10)
            assert 0.0 <= lo <= hi <= 1.0
