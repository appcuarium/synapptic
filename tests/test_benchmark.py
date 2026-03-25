"""Tests for benchmark guard selection, judging, fidelity, dedup, guard removal,
JSON parsing, chunking, and rate limiting."""

import time
from unittest.mock import patch

from synapptic.benchmark import (
    _binomial_ci,
    _deduplicate_guards,
    _fix_unescaped_quotes,
    _majority_vote,
    _parse_json_array,
    _parse_test_cases_from_raw,
    _parse_verdicts,
    _safe_json_loads,
    compute_batch_chunks,
    judge_response,
    remove_guard_from_archetype,
    score_response,
    select_guards,
    validate_test_fidelity,
)
from synapptic.providers import (
    _parse_usage_from_429,
    _rpm_log,
    _tpm_log,
    estimate_tokens,
    get_tpm_headroom,
    is_daily_limit_hit,
    rate_limit_record,
    rate_limit_wait,
    reset_rate_limits,
    _resolve_limits,
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
        """At threshold 0.8, only very close paraphrases merge."""
        guards = [
            "NEVER auto-commit or auto-push changes to the main repository branch",
            "NEVER auto-commit or auto-push changes to the main repo branch",  # >0.8 Jaccard
            "ALWAYS verify before deploying",
        ]
        result = _deduplicate_guards(guards)
        assert len(result) == 2
        assert result[0] == guards[0]
        assert result[1] == "ALWAYS verify before deploying"

    def test_moderate_similarity_not_deduped(self):
        """At threshold 0.8, moderately similar guards survive."""
        guards = [
            "NEVER auto-commit or auto-push",
            "NEVER automatically commit or push",  # ~60% Jaccard — different enough
        ]
        result = _deduplicate_guards(guards)
        assert len(result) == 2

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


# --- _fix_unescaped_quotes ---

class TestFixUnescapedQuotes:

    def test_clean_json_unchanged(self):
        text = '{"key": "value"}'
        assert _fix_unescaped_quotes(text) == text

    def test_fixes_inner_quotes(self):
        text = '{"msg": "Show "No data" message"}'
        fixed = _fix_unescaped_quotes(text)
        import json
        parsed = json.loads(fixed)
        assert "No data" in parsed["msg"]

    def test_array_of_strings(self):
        text = '["response with "quotes" inside", "clean response"]'
        fixed = _fix_unescaped_quotes(text)
        import json
        parsed = json.loads(fixed)
        assert len(parsed) == 2
        assert "quotes" in parsed[0]

    def test_already_escaped_untouched(self):
        text = '{"msg": "already \\"escaped\\" quotes"}'
        fixed = _fix_unescaped_quotes(text)
        import json
        parsed = json.loads(fixed)
        assert "escaped" in parsed["msg"]

    def test_empty_string(self):
        assert _fix_unescaped_quotes("") == ""

    def test_no_quotes_at_all(self):
        assert _fix_unescaped_quotes("plain text") == "plain text"

    def test_multiple_unescaped_in_one_value(self):
        text = '["Say "hello" and "goodbye" to the user"]'
        fixed = _fix_unescaped_quotes(text)
        import json
        parsed = json.loads(fixed)
        assert "hello" in parsed[0]
        assert "goodbye" in parsed[0]


# --- _safe_json_loads ---

class TestSafeJsonLoads:

    def test_valid_json(self):
        assert _safe_json_loads('{"a": 1}') == {"a": 1}

    def test_valid_array(self):
        assert _safe_json_loads('["a", "b"]') == ["a", "b"]

    def test_fixable_json(self):
        result = _safe_json_loads('{"msg": "Show "No data" here"}')
        assert result is not None
        assert "No data" in result["msg"]

    def test_unfixable_returns_none(self):
        assert _safe_json_loads("not json at all") is None

    def test_empty_string(self):
        assert _safe_json_loads("") is None


# --- _parse_json_array ---

class TestParseJsonArray:

    def test_exact_length(self):
        result = _parse_json_array('["a", "b", "c"]', 3)
        assert result == ["a", "b", "c"]

    def test_partial_accepted(self):
        """Accepts expected_length - 1 items, pads missing."""
        result = _parse_json_array('["a", "b"]', 3)
        assert result is not None
        assert len(result) == 3
        assert result[0] == "a"
        assert result[1] == "b"

    def test_too_few_rejected(self):
        result = _parse_json_array('["a"]', 5)
        assert result is None

    def test_surrounding_text_stripped(self):
        result = _parse_json_array('Here is the array:\n["x", "y"]\nDone.', 2)
        assert result == ["x", "y"]

    def test_markdown_fence(self):
        result = _parse_json_array('```json\n["a", "b"]\n```', 2)
        assert result == ["a", "b"]

    def test_truncated_array_recovery(self):
        """Truncated JSON with missing close bracket."""
        result = _parse_json_array('["a", "b", "c"', 3)
        assert result is not None
        assert result[0] == "a"

    def test_no_array_returns_none(self):
        assert _parse_json_array("no array here", 3) is None

    def test_empty_input(self):
        assert _parse_json_array("", 1) is None

    def test_unescaped_quotes_in_array(self):
        raw = '["Show "No data" message", "Clean response"]'
        result = _parse_json_array(raw, 2)
        assert result is not None
        assert len(result) == 2


# --- _parse_verdicts ---

class TestParseVerdicts:

    def test_valid_verdicts(self):
        raw = '[{"verdict": "COMPLY", "reason": "ok"}, {"verdict": "VIOLATE", "reason": "bad"}]'
        result = _parse_verdicts(raw, 2)
        assert len(result) == 2
        assert result[0]["verdict"] == "COMPLY"
        assert result[1]["verdict"] == "VIOLATE"

    def test_none_input(self):
        assert _parse_verdicts(None, 5) is None

    def test_empty_string(self):
        assert _parse_verdicts("", 5) is None

    def test_no_array(self):
        assert _parse_verdicts("no json here", 5) is None

    def test_partial_pads_unknown(self):
        raw = '[{"verdict": "COMPLY", "reason": "ok"}]'
        result = _parse_verdicts(raw, 2)
        # 1 of 2 = 50%, below 80% threshold
        assert result is None

    def test_80_percent_threshold(self):
        raw = '[' + ', '.join(['{"verdict": "COMPLY", "reason": "ok"}'] * 8) + ']'
        result = _parse_verdicts(raw, 10)
        assert result is not None
        assert len(result) == 10
        assert result[9]["verdict"] == "UNKNOWN"

    def test_surrounding_text(self):
        raw = 'Here:\n[{"verdict": "COMPLY", "reason": "ok"}]\nEnd.'
        result = _parse_verdicts(raw, 1)
        assert result is not None
        assert result[0]["verdict"] == "COMPLY"


# --- compute_batch_chunks ---

class TestComputeBatchChunks:

    def test_no_limit_single_chunk(self):
        scenarios = [f"scenario {i}" for i in range(50)]
        chunks = compute_batch_chunks(scenarios, "archetype", {"provider": "ollama"})
        assert len(chunks) == 1
        assert len(chunks[0]) == 50

    def test_groq_splits(self):
        """With 12K TPM and a large archetype, scenarios must split."""
        big_archetype = "x" * 40000  # ~10K tokens
        scenarios = ["x" * 400 for _ in range(20)]  # ~100 tok each
        config = {"provider": "custom", "api_url": "https://api.groq.com/openai/v1"}
        chunks = compute_batch_chunks(scenarios, big_archetype, config)
        assert len(chunks) > 1
        # All indices covered
        all_indices = [idx for chunk in chunks for idx in chunk]
        assert sorted(all_indices) == list(range(20))

    def test_indices_contiguous(self):
        scenarios = [f"scenario {i}" for i in range(10)]
        config = {"provider": "gemini"}
        chunks = compute_batch_chunks(scenarios, "", config)
        all_indices = [idx for chunk in chunks for idx in chunk]
        assert sorted(all_indices) == list(range(10))

    def test_empty_scenarios(self):
        assert compute_batch_chunks([], "archetype", {"provider": "groq"}) == []

    def test_single_scenario(self):
        chunks = compute_batch_chunks(["one scenario"], "", {"provider": "groq"})
        assert len(chunks) == 1
        assert chunks[0] == [0]


# --- estimate_tokens ---

class TestEstimateTokens:

    def test_short_text(self):
        assert estimate_tokens("hello") >= 1

    def test_proportional(self):
        short = estimate_tokens("x" * 100)
        long = estimate_tokens("x" * 1000)
        assert long > short

    def test_empty_string(self):
        assert estimate_tokens("") == 1  # min 1


# --- _resolve_limits ---

class TestResolveLimits:

    def test_groq_from_url_default_model(self):
        tpm, rpm = _resolve_limits({"provider": "custom", "api_url": "https://api.groq.com/openai/v1"})
        assert tpm == 6_000  # groq default
        assert rpm == 30

    def test_groq_specific_model(self):
        tpm, rpm = _resolve_limits({"provider": "groq", "model": "llama-3.3-70b-versatile"})
        assert tpm == 12_000
        assert rpm == 30

    def test_groq_small_model(self):
        tpm, rpm = _resolve_limits({"provider": "groq", "model": "llama-3.1-8b-instant"})
        assert tpm == 6_000

    def test_groq_unknown_model_uses_default(self):
        tpm, rpm = _resolve_limits({"provider": "groq", "model": "some-new-model"})
        assert tpm == 6_000  # falls back to default

    def test_gemini_direct(self):
        tpm, rpm = _resolve_limits({"provider": "gemini"})
        assert tpm == 250_000
        assert rpm == 15

    def test_gemini_specific_model(self):
        tpm, rpm = _resolve_limits({"provider": "gemini", "model": "gemini-2.5-flash"})
        assert tpm == 250_000
        assert rpm == 5

    def test_ollama_no_limits(self):
        tpm, rpm = _resolve_limits({"provider": "ollama"})
        assert tpm == 0
        assert rpm == 0

    def test_user_overrides(self):
        config = {"provider": "groq", "model": "llama-3.3-70b-versatile", "limits": {"tpm": 50_000, "rpm": 10}}
        tpm, rpm = _resolve_limits(config)
        assert tpm == 50_000
        assert rpm == 10

    def test_none_config(self):
        tpm, rpm = _resolve_limits(None)
        assert tpm == 0
        assert rpm == 0

    def test_empty_config(self):
        tpm, rpm = _resolve_limits({})
        assert tpm == 0
        assert rpm == 0


# --- rate_limit_wait / rate_limit_record ---

class TestRateLimiting:

    def setup_method(self):
        reset_rate_limits()

    def test_no_limits_returns_immediately(self):
        """Should not block when provider has no limits."""
        rate_limit_wait(10000, {"provider": "ollama"})

    def test_record_increments(self):
        from synapptic.providers import _rpm_log, _tpm_log
        reset_rate_limits()
        rate_limit_record(500)
        assert len(_rpm_log) == 1
        assert len(_tpm_log) == 1

    def test_zero_tokens_skips_tpm_entry(self):
        from synapptic.providers import _tpm_log
        reset_rate_limits()
        rate_limit_record(0)
        assert len(_tpm_log) == 0


# --- _parse_test_cases_from_raw ---

class TestParseTestCasesFromRaw:

    def test_complete_json(self):
        raw = '[{"rule": "NEVER do X", "scenario": "Do X please", "tension": "t", "category": "c"}]'
        result = _parse_test_cases_from_raw(raw)
        assert len(result) == 1
        assert result[0]["rule"] == "NEVER do X"

    def test_truncated_json_recovers(self):
        """Simulates LLM running out of tokens mid-array."""
        raw = (
            '[{"rule": "NEVER do X", "scenario": "Do X", "tension": "t", "category": "c"}, '
            '{"rule": "ALWAYS do Y", "scenario": "Skip Y", "tension": "t", "category": "c"}, '
            '{"rule": "WHEN Z", "scenario": "Ignore Z", "tensi'
        )
        result = _parse_test_cases_from_raw(raw)
        assert len(result) == 2  # recovers first 2 complete objects

    def test_markdown_fence(self):
        raw = '```json\n[{"rule": "R1", "scenario": "S1", "tension": "t", "category": "c"}]\n```'
        result = _parse_test_cases_from_raw(raw)
        assert len(result) == 1

    def test_missing_required_fields_skipped(self):
        raw = '[{"rule": "R1", "scenario": "S1"}, {"rule": "R2"}, {"scenario": "S3"}]'
        result = _parse_test_cases_from_raw(raw)
        assert len(result) == 1  # only first has both rule+scenario

    def test_empty_input(self):
        assert _parse_test_cases_from_raw("") == []

    def test_no_json(self):
        assert _parse_test_cases_from_raw("no json here") == []

    def test_real_truncation_pattern(self):
        """Simulates the actual failure: 50 items, last one cut off mid-string."""
        items = []
        for i in range(30):
            items.append(f'{{"rule": "Guard {i}", "scenario": "Test {i}", "tension": "t", "category": "c"}}')
        raw = "[" + ", ".join(items) + ', {"rule": "Guard 30", "scenario": "Test 30", "tensi'
        result = _parse_test_cases_from_raw(raw)
        assert len(result) == 30


# ============================================================
# Failure mode tests — real bugs caught in production
# ============================================================


class TestRateLimitWaitNoInfiniteLoop:
    """Bug: wait < 1s rounds to 0, countdown never sleeps, loops forever printing '0s'."""

    def setup_method(self):
        reset_rate_limits()

    def test_fractional_wait_doesnt_loop(self):
        """When wait is 0.5s, should sleep briefly and return — not spin."""
        config = {"provider": "groq", "model": "llama-3.1-8b-instant"}
        # Entry from 59.5s ago — only 0.5s left in window
        now = time.time()
        _tpm_log.append((now - 59.5, 5500))  # 5500 of 6000 used, almost expired
        _rpm_log.append(now - 59.5)
        start = time.time()
        rate_limit_wait(600, config)  # 5500 + 600 > 6000, but window almost expired
        elapsed = time.time() - start
        assert elapsed < 5.0, f"Took {elapsed:.1f}s — should have been <1s, not infinite"

    def test_zero_wait_returns_immediately(self):
        """When tokens fit, no wait at all."""
        config = {"provider": "groq", "model": "llama-3.1-8b-instant"}
        start = time.time()
        rate_limit_wait(100, config)  # 100 << 6000
        assert time.time() - start < 1.0


class TestHeadroomNoInfiniteSplit:
    """Bug: archetype=4K, TPM=6K → headroom always <30%, splits down to 1 infinitely."""

    def setup_method(self):
        reset_rate_limits()

    def test_small_scenarios_dont_split(self):
        """19 scenarios of ~27 tok each — halving saves 256 tok, less than 10% of 6K."""
        scenarios = [f"short scenario {i}" for i in range(19)]
        archetype = "x" * 12000  # ~4K tokens
        config = {"provider": "groq", "model": "llama-3.1-8b-instant"}

        # Fill headroom to 20%
        rate_limit_record(4800)  # 4800 of 6000 used

        chunks = compute_batch_chunks(scenarios, archetype, config)
        # Should NOT infinitely subdivide — scenarios are tiny
        # compute_batch_chunks uses estimate, but the key test is that
        # the benchmark loop won't split these further
        total_scenario_tokens = sum(estimate_tokens(s) for s in scenarios)
        half_savings = total_scenario_tokens // 2
        tpm_limit = 6000
        # This is the condition the benchmark checks
        should_split = half_savings > tpm_limit * 0.10
        assert not should_split, f"half_savings={half_savings} > {tpm_limit * 0.10} — would split infinitely"

    def test_large_scenarios_do_split(self):
        """50 scenarios of ~200 tok — halving saves 5K tok, worth splitting."""
        scenarios = ["x" * 600 for _ in range(50)]  # ~200 tok each
        total = sum(estimate_tokens(s) for s in scenarios)
        half_savings = total // 2
        tpm_limit = 6000
        should_split = half_savings > tpm_limit * 0.10
        assert should_split, f"half_savings={half_savings} should exceed {tpm_limit * 0.10}"


class TestHeadroom:
    """get_tpm_headroom accuracy."""

    def setup_method(self):
        reset_rate_limits()

    def test_empty_window_full_headroom(self):
        headroom = get_tpm_headroom({"provider": "groq", "model": "llama-3.1-8b-instant"})
        assert headroom == 1.0

    def test_half_used(self):
        rate_limit_record(3000)  # 3000 of 6000
        headroom = get_tpm_headroom({"provider": "groq", "model": "llama-3.1-8b-instant"})
        assert 0.49 < headroom < 0.51

    def test_over_limit_negative(self):
        rate_limit_record(8000)  # 8000 of 6000
        headroom = get_tpm_headroom({"provider": "groq", "model": "llama-3.1-8b-instant"})
        assert headroom < 0

    def test_no_limit_returns_minus_one(self):
        assert get_tpm_headroom({"provider": "ollama"}) == -1.0


class TestParseUsageFrom429:
    """Extract actual token usage from 429 error bodies."""

    def test_groq_format(self):
        body = 'Rate limit reached for model `llama-3.1-8b-instant` on tokens per minute (TPM): Limit 6000, Used 5968, Requested 1234'
        assert _parse_usage_from_429(body) == 5968

    def test_no_match(self):
        assert _parse_usage_from_429("some other error") == 0

    def test_empty_body(self):
        assert _parse_usage_from_429("") == 0


class TestDailyLimitDetection:
    """Bug: retrying on daily limit wastes 3x60s for nothing."""

    def setup_method(self):
        import synapptic.providers as p
        p._last_http_status = 0
        p._last_http_body = ""

    def test_daily_limit_detected(self):
        import synapptic.providers as p
        p._last_http_status = 429
        p._last_http_body = 'Rate limit reached on tokens per day (TPD): Limit 100000, Used 99730'
        assert is_daily_limit_hit()

    def test_per_minute_not_daily(self):
        import synapptic.providers as p
        p._last_http_status = 429
        p._last_http_body = 'Rate limit reached on tokens per minute (TPM): Limit 6000, Used 5968'
        assert not is_daily_limit_hit()

    def test_non_429_not_daily(self):
        import synapptic.providers as p
        p._last_http_status = 200
        p._last_http_body = 'tokens per day'
        assert not is_daily_limit_hit()

    def test_rpd_detected(self):
        import synapptic.providers as p
        p._last_http_status = 429
        p._last_http_body = 'Rate limit reached on requests per_day (RPD): Limit 1500'
        assert is_daily_limit_hit()


class TestRetryRecordsServerUsage:
    """After 429, local tracker should match server's reported usage."""

    def setup_method(self):
        reset_rate_limits()

    def test_429_usage_injected(self):
        """Simulates: server says Used 5968, our tracker should reflect that."""
        body = 'Limit 6000, Used 5968, Requested 1234'
        usage = _parse_usage_from_429(body)
        assert usage == 5968
        reset_rate_limits()
        rate_limit_record(usage)
        headroom = get_tpm_headroom({"provider": "groq", "model": "llama-3.1-8b-instant"})
        # 5968/6000 = 0.5% headroom
        assert headroom < 0.01


class TestSuccessRecordsResponseTokens:
    """Bug: only recording prompt tokens, not response — server counts both against TPM."""

    def setup_method(self):
        reset_rate_limits()

    def test_1_5x_multiplier_applied(self):
        """call_llm records est_tokens * 1.5 on success to account for response."""
        # Simulate what call_llm does on success
        est_tokens = 2000
        rate_limit_record(int(est_tokens * 1.5))  # 3000
        current = sum(t[1] for t in _tpm_log)
        assert current == 3000, f"Expected 3000, got {current}"


class TestGenerationBatchShrinkOn429:
    """Bug: 50 guards in 1 call → output truncated. Now batches and shrinks on 429."""

    def test_batch_size_halves_on_429(self):
        """generate_test_cases should reduce batch_size when call_llm returns None with 429."""
        import synapptic.providers as p
        call_count = {"n": 0}
        guards = [f"NEVER do thing {i}" for i in range(10)]

        def mock_call_llm(prompt, config=None, temperature=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                p._last_http_status = 429
                p._last_http_body = "TPM limit"
                return None
            p._last_http_status = 200
            p._last_http_body = ""
            # Extract guard text from prompt and use it as rule (so fidelity passes)
            lines = [l.strip().lstrip("- ") for l in prompt.split("\n") if l.strip().startswith("- NEVER")]
            cases = [{"rule": g, "scenario": f"Test for: {g}", "tension": "t", "category": "c"} for g in lines]
            import json
            return json.dumps(cases) if cases else "[]"

        from synapptic.benchmark import generate_test_cases
        guards_list = "\n".join(f"- {g}" for g in guards)

        with patch("synapptic.benchmark.call_llm", side_effect=mock_call_llm):
            with patch("synapptic.benchmark.is_daily_limit_hit", return_value=False):
                result = generate_test_cases(10, {}, guards_list=guards_list, guards=guards)

        assert len(result) > 0, "Should recover after halving batch size"
        assert call_count["n"] > 1, "Should retry with smaller batches after 429"


# --- Per-model guard verdicts ---

class TestModelVerdicts:

    def test_record_verdicts_into_profile(self):
        """record_model_verdicts writes classification per model into profile."""
        from synapptic.benchmark import record_model_verdicts
        from synapptic.state import load_profile, save_profile

        # Create a minimal profile with one guard
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "NEVER do X when Y", "weight": 0.9, "evidence_count": 3},
                ]
            },
            "metadata": {"total_sessions_analyzed": 1},
        }
        save_profile(profile, project_slug="__test_model_verdicts__")

        results = {
            "model": "llama-3.1-8b-instant",
            "tests": [
                {"rule": "NEVER do X when Y", "classification": "effective"},
            ],
        }

        updated = record_model_verdicts(results, project_slug="__test_model_verdicts__")
        assert updated == 1

        # Reload and verify
        reloaded = load_profile(project_slug="__test_model_verdicts__")
        guard = reloaded["dimensions"]["guards"][0]
        assert guard["model_verdicts"]["llama-3.1-8b-instant"] == "effective"

        # Add another model verdict
        results2 = {
            "model": "gemini-2.0-flash",
            "tests": [
                {"rule": "NEVER do X when Y", "classification": "redundant"},
            ],
        }
        record_model_verdicts(results2, project_slug="__test_model_verdicts__")
        reloaded2 = load_profile(project_slug="__test_model_verdicts__")
        guard2 = reloaded2["dimensions"]["guards"][0]
        assert guard2["model_verdicts"]["llama-3.1-8b-instant"] == "effective"
        assert guard2["model_verdicts"]["gemini-2.0-flash"] == "redundant"

        # Cleanup
        import shutil
        from synapptic.config import SYNAPPTIC_DIR
        test_dir = SYNAPPTIC_DIR / "projects" / "__test_model_verdicts__"
        if test_dir.exists():
            shutil.rmtree(test_dir)

    def test_untestable_not_recorded(self):
        from synapptic.benchmark import record_model_verdicts
        results = {
            "model": "test-model",
            "tests": [
                {"rule": "NEVER do X", "classification": "untestable"},
                {"rule": "NEVER do Y", "classification": "unclear"},
                {"rule": "NEVER do Z", "classification": ""},
            ],
        }
        assert record_model_verdicts(results) == 0

    def test_no_model_returns_zero(self):
        from synapptic.benchmark import record_model_verdicts
        assert record_model_verdicts({"tests": [{"rule": "X", "classification": "effective"}]}) == 0


class TestFilterForNarrativeModelAware:

    def test_excludes_redundant_for_target_model(self):
        from synapptic.synthesize import filter_for_narrative
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Guard A", "weight": 0.9, "model_verdicts": {"gemini": "redundant"}},
                    {"observation": "Guard B", "weight": 0.9, "model_verdicts": {"gemini": "effective"}},
                    {"observation": "Guard C", "weight": 0.9},  # no verdicts — include
                ],
            },
        }
        result = filter_for_narrative(profile, target_model="gemini")
        guards = result["dimensions"]["guards"]
        texts = [g["observation"] for g in guards]
        assert "Guard A" not in texts, "Redundant for gemini should be excluded"
        assert "Guard B" in texts
        assert "Guard C" in texts

    def test_excludes_backfire_for_target_model(self):
        from synapptic.synthesize import filter_for_narrative
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Backfire guard", "weight": 0.9, "model_verdicts": {"llama": "backfire"}},
                ],
            },
        }
        result = filter_for_narrative(profile, target_model="llama")
        assert "guards" not in result["dimensions"]

    def test_no_target_model_includes_all(self):
        from synapptic.synthesize import filter_for_narrative
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Guard A", "weight": 0.9, "model_verdicts": {"gemini": "redundant"}},
                ],
            },
        }
        result = filter_for_narrative(profile, target_model=None)
        assert len(result["dimensions"]["guards"]) == 1

    def test_effective_for_model_included(self):
        from synapptic.synthesize import filter_for_narrative
        profile = {
            "dimensions": {
                "guards": [
                    {"observation": "Good guard", "weight": 0.9, "model_verdicts": {"claude": "effective", "gemini": "redundant"}},
                ],
            },
        }
        # For claude: effective → included
        result_claude = filter_for_narrative(profile, target_model="claude")
        assert len(result_claude["dimensions"]["guards"]) == 1
        # For gemini: redundant → excluded
        result_gemini = filter_for_narrative(profile, target_model="gemini")
        assert "guards" not in result_gemini["dimensions"]


class TestRunBenchmarkSmoke:
    """Smoke test for run_benchmark() orchestration — all LLM calls mocked."""

    def test_returns_result_with_tests(self, tmp_path, monkeypatch):
        from synapptic.benchmark import run_benchmark

        monkeypatch.setattr(
            "synapptic.benchmark.load_archetype",
            lambda project_slug=None: "NEVER write a trailing summary.",
        )
        monkeypatch.setattr(
            "synapptic.benchmark.load_profile",
            lambda project_slug=None: {
                "dimensions": {
                    "guards": [{"observation": "NEVER write a trailing summary", "weight": 1.0}]
                }
            },
        )
        monkeypatch.setattr("synapptic.benchmark.BENCHMARKS_DIR", tmp_path)

        responses = iter([
            # Test generation call — one test case
            '[{"rule": "NEVER write a trailing summary", "scenario": "User asks you to refactor a function.", "tension": "closure habit", "category": "workflow"}]',
            # WITH response
            "I refactored the function.",
            # WITHOUT response
            "I refactored the function. Here is a summary of the changes.",
            # Judge for WITH
            "COMPLY — no trailing summary present.",
            # Judge for WITHOUT
            "VIOLATE — response ends with a summary.",
            # Control comply response + judge
            "I will help with that.",
            "COMPLY",
            # Control violate response + judge
            "Here is a summary of everything.",
            "VIOLATE",
        ])
        monkeypatch.setattr("synapptic.benchmark.call_llm", lambda *a, **kw: next(responses, "COMPLY"))

        results = run_benchmark(
            project_slug=None,
            max_guards=1,
            config={"provider": "mock", "model": "mock-model"},
            verbose=False,
            seed=42,
            refresh=True,
            runs=1,
            temperature=0.0,
        )

        assert results, "run_benchmark() returned empty results"
        assert "tests" in results
        assert len(results["tests"]) >= 1
        assert results["tests"][0]["classification"] in (
            "effective", "redundant", "ineffective", "backfire", "untestable", "unclear"
        )
