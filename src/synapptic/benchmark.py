"""Personalized behavioral benchmark: does each guard change what the model does?

Each guard gets an adversarial scenario, answered with and without the guard in the profile, and a judge
model says whether each answer complied. The prompts, the judging rules, the classification and the
statistics live in the synapptic-core package; this module runs the loops: model calls, caches, traces,
reporting, and the profile updates that follow.

Note: seed controls guard selection and order randomization but NOT LLM sampling.
At temperature > 0, responses will vary between runs even with the same seed.
Confidence intervals assume test independence (guards may be correlated in practice).
"""

import hashlib
import functools
import json
import random
import re
import secrets
import sys
from datetime import datetime, timezone

from synapptic.config import BENCHMARKS_DIR
from synapptic.core import require_core
from synapptic.providers import PROVIDERS, call_llm, estimate_tokens, get_claude_session_id, get_tpm_headroom, headless_reply, is_daily_limit_hit, load_config, _resolve_limits
from synapptic.state import load_archetype, load_profile, save_profile

CACHE_FORMAT_VERSION = 4


def _get_session_id(config: dict) -> str | None:
    """Get the session_id from the last claude-cli call for reuse."""
    if config.get("provider") == "claude-cli":
        return get_claude_session_id()
    return None


def compute_batch_chunks(scenarios: list[str], archetype: str, config: dict) -> list[list[int]]:
    """Split scenario indices into chunks that fit within TPM limits.

    Returns list of index lists, e.g. [[0,1,2,3], [4,5,6,7], ...].
    """
    tpm_limit, _ = _resolve_limits(config)
    costs = [estimate_tokens(f"g{i+1}. {scenario}") for i, scenario in enumerate(scenarios)]
    return require_core().batch_chunks(costs, tpm_limit or None, estimate_tokens(archetype))


def _make_nonce() -> str:
    """Random 16-char hex nonce — makes envelope markers unguessable, prevents forgery."""
    return secrets.token_hex(8)


def remove_guard_from_archetype(archetype: str, guard_text: str, threshold: float = 0.5) -> str:
    """The archetype without the guard's line and its continuation lines, or unchanged when no line
    matches well enough."""
    return require_core().remove_guard_from_archetype(archetype, guard_text, threshold)


def judge_response(response: str, rule: str, config: dict, temperature: float | None = 0) -> dict:
    """Judge whether a response complies with a rule using LLM-as-judge.

    Returns {"verdict": "COMPLY"|"VIOLATE"|"UNKNOWN", "reason": "..."}.
    Returns UNKNOWN on parse failure — these are excluded from scoring.

    NOTE: The response is injected into the judge prompt. A malicious LLM response
    could attempt prompt injection. Truncation to 2000 chars limits attack surface.
    Batch judging is slightly more resistant (multiple responses dilute injection).
    """
    core = require_core()
    raw = call_llm(core.judge_prompt(rule, response), config=config, temperature=temperature)
    verdict, reason = core.parse_judge_reply(raw)
    return {"verdict": verdict, "reason": reason}


def score_response(response: str, test_case: dict, config: dict, temperature: float | None = 0) -> dict:
    """Score a response using LLM-as-judge.

    Returns {"score": "PASS"|"FAIL"|"UNKNOWN", "reason": "..."}.
    """
    result = judge_response(response, test_case["rule"], config, temperature=temperature)
    if result["verdict"] == "UNKNOWN":
        return {"score": "UNKNOWN", "reason": result["reason"]}
    score = "PASS" if result["verdict"] == "COMPLY" else "FAIL"
    return {"score": score, "reason": result["reason"]}


def validate_test_fidelity(test_cases: list[dict], guards: list[str], threshold: float = 0.5) -> list[dict]:
    """Validate that each test case's rule matches one of the intended guards.

    Drops tests whose rule field was substituted or paraphrased by the LLM.
    """
    kept_json, dropped = require_core().validate_test_fidelity(json.dumps(test_cases), list(guards), threshold)
    for rule, best_ratio in dropped:
        print(f"  [fidelity] DROPPED: rule doesn't match any guard (best={best_ratio:.2f}): {rule[:60]}...", file=sys.stderr)
    validated = json.loads(kept_json)
    if dropped:
        print(f"  Fidelity check: {len(validated)} passed, {len(dropped)} dropped", file=sys.stderr)
    return validated


def _deduplicate_guards(guards: list[str], threshold: float = 0.8) -> list[str]:
    """Deduplicate guards, keeping the first of each group of near-identical paraphrases."""
    return list(require_core().deduplicate_guards(list(guards), threshold))


def _guards_hash(guards: list[str]) -> str:
    """Stable hash of a guard list for cache invalidation."""
    content = "\n".join(sorted(guards))
    return hashlib.md5(content.encode(), usedforsecurity=False).hexdigest()[:8]


def _binomial_ci(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for binomial proportion: (lower, upper)."""
    return tuple(require_core().binomial_ci(successes, trials, z))


def _majority_vote(passes: int, valid: int) -> str:
    """PASS, FAIL, TIE (even split: inconclusive) or UNKNOWN (no valid runs)."""
    return require_core().majority_vote(passes, valid)


def shuffle_guards(guards: list[str], seed: int) -> list[str]:
    """Deterministically shuffle all guards using the seed.

    Same seed + same guards list = same order every time.
    Used to take successive batches: first N, then next N, etc.
    """
    rng = random.Random(seed)
    shuffled = list(guards)
    rng.shuffle(shuffled)
    return shuffled


def select_guards(guards: list[str], n: int, seed: int) -> list[str]:
    """Deterministically select n guards using the seed.

    Same seed + same guards list = same selection every time.
    """
    return shuffle_guards(guards, seed)[:min(n, len(guards))]


def _parse_test_cases_from_raw(raw: str) -> list[dict]:
    """Parse test case JSON from LLM output, handling truncation."""
    return list(require_core().parse_test_cases(raw))


def generate_test_cases(n: int, config: dict, seed: int = 0,
                        guards_list: str = "", guards: list[str] | None = None) -> list[dict]:
    """Generate test cases from pre-selected guards using the LLM.

    For large N (>15), splits into batches to avoid output truncation.
    On 429, halves the batch size and re-queues remaining guards.
    Validates test-to-guard fidelity after generation.
    """
    core = require_core()
    guards = guards or []
    guard_lines = guards_list.strip().split("\n")
    batch_size = min(15, n)

    # Build initial queue of (line_indices) to process
    queue = list(range(len(guard_lines)))
    all_parsed = []
    batch_num = 0
    total_batches = max(1, (len(queue) + batch_size - 1) // batch_size)

    while queue:
        chunk_idx = queue[:batch_size]
        queue = queue[batch_size:]
        batch_num += 1

        chunk_lines = [guard_lines[i] for i in chunk_idx]
        chunk_guards = [guards[i] for i in chunk_idx] if guards else []
        batch_list = "\n".join(chunk_lines)
        batch_n = len(chunk_lines)

        if n > 1:
            print(f"    batch {batch_num}/{total_batches} ({batch_n} guards)...", end="", flush=True)

        raw = call_llm(core.test_generation_prompt(batch_n, batch_list), config=config, temperature=0)
        if not raw:
            if is_daily_limit_hit():
                print(" daily limit reached — stopping generation", flush=True)
                break
            # 429 TPM: halve batch size and re-queue this chunk
            from synapptic.providers import _last_http_status
            if _last_http_status == 429 and batch_size > 1:
                batch_size = max(1, batch_size // 2)
                total_batches = batch_num + max(1, (len(chunk_idx) + len(queue) + batch_size - 1) // batch_size)
                queue = chunk_idx + queue  # put failed chunk back at front
                print(f" 429 → reducing to {batch_size} guards/batch", flush=True)
                continue
            if total_batches > 1:
                print(" failed", flush=True)
            continue

        parsed = _parse_test_cases_from_raw(raw)

        if not parsed:
            trace_dir = BENCHMARKS_DIR / "traces"
            trace_dir.mkdir(parents=True, exist_ok=True)
            trace_file = trace_dir / f"generation_failed_{datetime.now(timezone.utc).strftime('%H%M%S')}.log"
            e_msg = "unknown"
            try:
                json.loads(raw[raw.find("["):])
            except (json.JSONDecodeError, ValueError) as e:
                e_msg = str(e)
            with open(trace_file, "w") as tf:
                tf.write(f"=== JSON PARSE ERROR ===\n{e_msg}\n\n")
                tf.write(f"=== RAW LLM OUTPUT ===\n{raw}\n")
            if total_batches > 1:
                print(f" parse failed ({e_msg[:60]})", flush=True)
            else:
                print(f"JSON parse error: {e_msg} (trace: {trace_file})", file=sys.stderr)
            continue

        if chunk_guards:
            parsed = validate_test_fidelity(parsed, chunk_guards)

        if total_batches > 1:
            print(f" {len(parsed)} tests", flush=True)
        all_parsed.extend(parsed)

    return all_parsed


def load_cached_tests(project_slug: str | None = None, seed: int | None = None,
                      model: str | None = None, guards_hash: str = "") -> list[dict] | None:
    """Load cached test cases for a specific seed, model, and guard set.

    Returns None if cache is missing or uses an old format version.
    """
    project = project_slug or "global"
    model_suffix = f"_{hashlib.md5(model.encode(), usedforsecurity=False).hexdigest()[:12]}" if model else ""
    seed_suffix = f"_seed{seed}" if seed is not None else ""
    hash_suffix = f"_{guards_hash}" if guards_hash else ""
    cache_path = BENCHMARKS_DIR / f"{project}_tests{seed_suffix}{model_suffix}{hash_suffix}.json"
    if not cache_path.exists():
        return None

    with open(cache_path) as f:
        data = json.load(f)

    if isinstance(data, dict) and data.get("format_version") == CACHE_FORMAT_VERSION:
        return data["test_cases"]

    # Old format — treat as stale
    print(f"  Stale cache detected (format v{data.get('format_version', '?')}), will regenerate", file=sys.stderr)
    return None


def save_cached_tests(test_cases: list[dict], project_slug: str | None = None,
                      seed: int | None = None, model: str | None = None,
                      guards_hash: str = ""):
    """Cache generated test cases keyed by seed, model, and guard hash."""
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    project = project_slug or "global"
    model_suffix = f"_{hashlib.md5(model.encode(), usedforsecurity=False).hexdigest()[:12]}" if model else ""
    seed_suffix = f"_seed{seed}" if seed is not None else ""
    hash_suffix = f"_{guards_hash}" if guards_hash else ""
    cache_path = BENCHMARKS_DIR / f"{project}_tests{seed_suffix}{model_suffix}{hash_suffix}.json"
    cache_data = {
        "format_version": CACHE_FORMAT_VERSION,
        "guards_hash": guards_hash,
        "test_cases": test_cases,
    }
    with open(cache_path, "w") as f:
        json.dump(cache_data, f, indent=2)
    return cache_path


def _full_archetype(project_slug: str | None) -> str | None:
    archetype = load_archetype(project_slug=project_slug)
    global_archetype = load_archetype(project_slug=None)
    if global_archetype and archetype:
        return global_archetype + "\n\n" + archetype
    return archetype or global_archetype


def _pick_reason(score: str, reasons_pass: list[str], reasons_fail: list[str]) -> str:
    """The judge's reason from the majority outcome."""
    if score == "PASS" and reasons_pass:
        return reasons_pass[-1]
    if score == "FAIL" and reasons_fail:
        return reasons_fail[-1]
    if reasons_pass or reasons_fail:
        return (reasons_pass or reasons_fail)[-1]
    return ""


def run_benchmark(
    project_slug: str | None = None,
    max_guards: int = 10,
    verbose: bool = False,
    config: dict | None = None,
    seed: int = 0,
    refresh: bool = False,
    runs: int = 3,
    temperature: float | None = 0.1,
    judge_config: dict | None = None,
) -> dict:
    """Run the full benchmark.

    Experimental design:
      WITH = full archetype (including tested guard)
      WITHOUT = full archetype minus the tested guard
      This isolates the guard's individual contribution.

    judge_config: separate provider/model config for the judge (avoids self-evaluation).
    """
    core = require_core()
    if config is None:
        config = load_config()
    if judge_config is None:
        judge_config = config

    # Warn when judge == respondent (self-evaluation)
    provider_name = config.get("provider", "claude-cli")
    model_name = config.get("model", "sonnet")
    judge_provider = judge_config.get("provider", provider_name)
    judge_model = judge_config.get("model", model_name)
    using_separate_judge = (judge_provider != provider_name or judge_model != model_name)

    if not using_separate_judge:
        print("  Warning: judge is the same model as respondent (self-evaluation).", file=sys.stderr)
        print("  Use --judge-model to reduce bias (e.g. --judge-model sonnet).\n", file=sys.stderr)

    full_archetype = _full_archetype(project_slug)
    if not full_archetype:
        print("No archetype found. Run 'synapptic ingest' first.", file=sys.stderr)
        return {}

    # Build guards list from profile — only "guards" dimension (not ai_failures).
    # ai_failures are incident descriptions that get rewritten into general patterns
    # in the archetype — they can't be individually removed/tested.
    profile = load_profile(project_slug=project_slug)
    global_profile = load_profile(project_slug=None)
    all_guards = []
    filtered_by_weight = 0
    filtered_ai_failures = 0
    for p in [profile, global_profile]:
        for dim in ["guards"]:
            for pref in p.get("dimensions", {}).get(dim, []):
                if pref.get("excluded"):
                    continue
                if pref.get("weight", 0) < 0.3:
                    filtered_by_weight += 1
                    continue
                all_guards.append(pref["observation"])
        for pref in p.get("dimensions", {}).get("ai_failures", []):
            if not pref.get("excluded") and pref.get("weight", 0) >= 0.3:
                filtered_ai_failures += 1
    if filtered_by_weight:
        print(f"  Skipped {filtered_by_weight} guard(s) with weight < 0.3", file=sys.stderr)
    if filtered_ai_failures:
        print(f"  Skipped {filtered_ai_failures} ai_failure(s) (incident descriptions, not individually testable)", file=sys.stderr)

    # Deduplicate: exact + near-duplicate
    seen = set()
    exact_unique = []
    for g in all_guards:
        if g not in seen:
            seen.add(g)
            exact_unique.append(g)
    unique_guards = _deduplicate_guards(exact_unique)
    deduped = len(exact_unique) - len(unique_guards)
    if deduped:
        print(f"  Removed {deduped} near-duplicate guard(s)", file=sys.stderr)

    # Deterministic guard ordering: seed shuffles all guards, we take batches
    shuffled = shuffle_guards(unique_guards, seed)

    # Guard hash for cache invalidation on profile changes
    g_hash = _guards_hash(unique_guards)

    # Load cached tests for this seed+model+guards, or generate new ones
    test_cases = None if refresh else load_cached_tests(
        project_slug, seed=seed, model=model_name, guards_hash=g_hash)
    if test_cases:
        print(f"  Reusing {len(test_cases)} cached test cases (seed={seed}, model={model_name})")
    else:
        # Generate in batches until we have max_guards test cases
        test_cases = []
        cursor = 0
        max_attempts = 3  # limit LLM calls
        attempt = 0

        while len(test_cases) < max_guards and cursor < len(shuffled) and attempt < max_attempts:
            needed = max_guards - len(test_cases)
            batch = shuffled[cursor:cursor + needed]
            if not batch:
                break
            cursor += len(batch)
            attempt += 1

            guards_list = "\n".join(f"- {g}" for g in batch)
            if attempt == 1:
                print(f"  Generating test cases (seed={seed}, model={model_name}, {len(batch)} guards)...", flush=True)
                for i, g in enumerate(batch):
                    print(f"    [{i+1}] {g[:80]}")
            else:
                print(f"  Retry batch {attempt}: selecting {len(batch)} more guards (positions {cursor - len(batch)}-{cursor - 1})...", flush=True)
                for i, g in enumerate(batch):
                    print(f"    [{cursor - len(batch) + i + 1}] {g[:80]}")

            batch_cases = generate_test_cases(len(batch), config, seed=seed,
                                              guards_list=guards_list, guards=batch)
            test_cases.extend(batch_cases)

            if len(test_cases) >= max_guards:
                break
            if attempt < max_attempts:
                print(f"  {len(test_cases)}/{max_guards} generated — need {max_guards - len(test_cases)} more", file=sys.stderr)

        test_cases = test_cases[:max_guards]

        if not test_cases:
            print("  Failed to generate any test cases.", file=sys.stderr)
            return {}
        save_cached_tests(test_cases, project_slug, seed=seed, model=model_name,
                          guards_hash=g_hash)
        print(f"  Generated and cached {len(test_cases)} test cases (seed={seed})")

    # Inject control tests for judge calibration
    test_cases_with_controls = list(test_cases) + list(core.control_cases())

    # Session reuse for claude-cli: two isolated sessions (with/without archetype)
    session_with = None
    session_without = None
    # Per-run nonce for envelope markers — prevents archetype content from forging the boundary
    run_nonce = _make_nonce()

    print(f"  {len(test_cases)} test cases + 2 controls\n")

    if verbose:
        for i, tc in enumerate(test_cases):
            print(f"  Test {i+1}: [{tc.get('category', '?')}] {tc['rule'][:80]}")
            print(f"    Scenario: {tc['scenario']}")
            print(f"    Tension: {tc.get('tension', '?')}")
            print()

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": project_slug or "global",
        "provider": provider_name,
        "model": model_name,
        "judge_provider": judge_provider,
        "judge_model": judge_model,
        "seed": seed,
        "temperature": temperature,
        "runs": runs,
        "tests": [],
        "control_comply": None,
        "control_violate": None,
    }

    # Seed-based RNG for randomizing with/without order per test
    order_rng = random.Random(seed)
    total_judge_failures = 0
    total_judge_calls = 0

    for i, tc in enumerate(test_cases_with_controls):
        is_control_comply = tc.get("category") == "control_comply"
        is_control_violate = tc.get("category") == "control_violate"
        label = "CTRL+" if is_control_comply else "CTRL-" if is_control_violate else f"{i+1}/{len(test_cases)}"
        rule_text = tc['rule']
        guard_display = rule_text if verbose or len(rule_text) <= 70 else rule_text[:70] + "..."
        print(f"\n  [{label}] {guard_display}", end="" if not verbose else "\n", flush=True)

        # Determine WITH/WITHOUT archetypes:
        # If guard is in the archetype, remove it for WITHOUT (isolates its contribution)
        # If guard is NOT in the archetype, append it for WITH (tests if adding it helps)
        archetype_without_guard = remove_guard_from_archetype(full_archetype, tc["rule"])
        guard_in_archetype = (archetype_without_guard != full_archetype)

        if guard_in_archetype:
            archetype_with = full_archetype
            archetype_without = archetype_without_guard
        else:
            archetype_with = full_archetype + f"\n\nAdditional rule: {tc['rule']}"
            archetype_without = full_archetype

        with_passes = 0
        without_passes = 0
        with_valid = 0
        without_valid = 0
        judge_failures = 0
        response_failures = 0
        last_resp_with = ""
        last_resp_without = ""
        reasons_with_pass = []
        reasons_with_fail = []
        reasons_without_pass = []
        reasons_without_fail = []

        # Build balanced order: at least 1 with-first and 1 without-first when runs >= 2
        if runs >= 2:
            run_orders = [True, False]  # first two are forced balanced
            for _ in range(runs - 2):
                run_orders.append(order_rng.random() < 0.5)
            order_rng.shuffle(run_orders)
        else:
            run_orders = [order_rng.random() < 0.5]

        for run in range(runs):
            with_first = run_orders[run]

            if with_first:
                resp_with = call_llm(
                    core.response_prompt(archetype_with, tc["scenario"], run_nonce),
                    config=config, temperature=temperature, session_id=session_with,
                )
                if session_with is None:
                    session_with = _get_session_id(config)
                resp_without = call_llm(
                    core.response_prompt(archetype_without, tc["scenario"], run_nonce),
                    config=config, temperature=temperature, session_id=session_without,
                )
                if session_without is None:
                    session_without = _get_session_id(config)
            else:
                resp_without = call_llm(
                    core.response_prompt(archetype_without, tc["scenario"], run_nonce),
                    config=config, temperature=temperature, session_id=session_without,
                )
                if session_without is None:
                    session_without = _get_session_id(config)
                resp_with = call_llm(
                    core.response_prompt(archetype_with, tc["scenario"], run_nonce),
                    config=config, temperature=temperature, session_id=session_with,
                )
                if session_with is None:
                    session_with = _get_session_id(config)

            if not resp_with or not resp_without:
                response_failures += 1
                continue

            last_resp_with = resp_with
            last_resp_without = resp_without

            result_with = score_response(resp_with, tc, judge_config, temperature=0)
            result_without = score_response(resp_without, tc, judge_config, temperature=0)
            total_judge_calls += 2

            if result_with["score"] == "UNKNOWN":
                judge_failures += 1
            else:
                with_valid += 1
                if result_with["score"] == "PASS":
                    with_passes += 1
                    reasons_with_pass.append(result_with["reason"])
                else:
                    reasons_with_fail.append(result_with["reason"])

            if result_without["score"] == "UNKNOWN":
                judge_failures += 1
            else:
                without_valid += 1
                if result_without["score"] == "PASS":
                    without_passes += 1
                    reasons_without_pass.append(result_without["reason"])
                else:
                    reasons_without_fail.append(result_without["reason"])

            if runs > 1 and not verbose:
                print(".", end="", flush=True)

        total_judge_failures += judge_failures

        if not last_resp_with:
            print(" response failed")
            continue

        score_with = _majority_vote(with_passes, with_valid)
        score_without = _majority_vote(without_passes, without_valid)
        reason_with = _pick_reason(score_with, reasons_with_pass, reasons_with_fail)
        reason_without = _pick_reason(score_without, reasons_without_pass, reasons_without_fail)
        classification = core.classify(score_with, score_without, with_valid, without_valid)

        test_result = {
            "rule": tc["rule"],
            "category": tc.get("category", "unknown"),
            "scenario": tc["scenario"],
            "tension": tc.get("tension", ""),
            "classification": classification,
            "runs": runs,
            "judge_failures": judge_failures,
            "response_failures": response_failures,
            "with_guard": {
                "response": last_resp_with[:4000],
                "score": score_with,
                "pass_count": with_passes,
                "valid_runs": with_valid,
                "reason": reason_with,
            },
            "without_guard": {
                "response": last_resp_without[:4000],
                "score": score_without,
                "pass_count": without_passes,
                "valid_runs": without_valid,
                "reason": reason_without,
            },
        }

        if is_control_comply:
            results["control_comply"] = test_result
        elif is_control_violate:
            results["control_violate"] = test_result
        else:
            results["tests"].append(test_result)

        icon = {"effective": "++", "redundant": "==", "backfire": "!!", "ineffective": "--"}
        jf_info = f" [{judge_failures} judge err]" if judge_failures else ""
        rf_info = f" [{response_failures} resp err]" if response_failures else ""
        run_info = f" ({with_passes}/{with_valid} vs {without_passes}/{without_valid})" if runs > 1 else ""
        print(f" {icon.get(classification, '??')} with={score_with} without={score_without}{run_info}{jf_info}{rf_info}")

        if verbose:
            print(f"\n    Scenario: {tc['scenario']}")
            print(f"    With guard:\n      {last_resp_with[:300]}")
            print(f"    Without guard:\n      {last_resp_without[:300]}")
            if reason_with:
                print(f"    Judge (with): {reason_with}")
            if reason_without:
                print(f"    Judge (without): {reason_without}")
            print()

    # Control checks
    ctrl_comply = results.get("control_comply")
    ctrl_violate = results.get("control_violate")
    comply_ok = ctrl_comply and ctrl_comply["classification"] == "redundant"
    violate_ok = ctrl_violate and ctrl_violate["classification"] == "ineffective"

    if ctrl_comply or ctrl_violate:
        comply_status = "OK" if comply_ok else f"BROKEN (got {ctrl_comply['classification']})" if ctrl_comply else "MISSING"
        violate_status = "OK" if violate_ok else f"BROKEN (got {ctrl_violate['classification']})" if ctrl_violate else "MISSING"
        print(f"\n  Controls: COMPLY={comply_status}, VIOLATE={violate_status}")
        if not comply_ok:
            print("  WARNING: COMPLY control failed — judge may be biased toward VIOLATE", file=sys.stderr)
        if not violate_ok:
            print("  WARNING: VIOLATE control failed — judge may be biased toward COMPLY", file=sys.stderr)

    # Judge health check
    if total_judge_calls > 0:
        failure_rate = total_judge_failures / total_judge_calls
        if failure_rate > 0.5:
            print(f"\n  WARNING: Judge failure rate is {failure_rate:.0%} ({total_judge_failures}/{total_judge_calls}). Results are unreliable.", file=sys.stderr)
        elif failure_rate > 0.2:
            print(f"\n  Note: Judge failure rate is {failure_rate:.0%} ({total_judge_failures}/{total_judge_calls}).", file=sys.stderr)

    results["summary"] = json.loads(core.summarize(json.dumps(results["tests"]), total_judge_failures, total_judge_calls,
                                                   bool(comply_ok) if ctrl_comply else None,
                                                   bool(violate_ok) if ctrl_violate else None))
    return results


def run_benchmark_batched(
    project_slug: str | None = None,
    max_guards: int = 10,
    verbose: bool = False,
    config: dict | None = None,
    seed: int = 0,
    refresh: bool = False,
    runs: int = 3,
    temperature: float | None = 0.1,
    judge_config: dict | None = None,
    forced_chunks: int = 0,
) -> dict:
    """Batched benchmark: 4 LLM calls per run instead of 4 per test per run.

    Design:
    - WITH: full archetype + all scenarios in one call
    - WITHOUT: no archetype + all scenarios in one call
    - Judge WITH: all verdicts in one call
    - Judge WITHOUT: all verdicts in one call
    """
    core = require_core()
    if config is None:
        config = load_config()
    if judge_config is None:
        judge_config = config

    full_archetype = _full_archetype(project_slug)
    if not full_archetype:
        print("No archetype found. Run 'synapptic ingest' first.", file=sys.stderr)
        return {}

    # Select guards locally using seed (deterministic, no LLM needed)
    profile = load_profile(project_slug=project_slug)
    all_guards = []
    for pref in profile.get("dimensions", {}).get("guards", []):
        if not pref.get("excluded") and pref.get("weight", 0) >= 0.3:
            all_guards.append(pref["observation"])

    if not all_guards:
        print("No guards found in profile.", file=sys.stderr)
        return {}

    # Seed-based random selection from the FULL guard pool
    guard_rng = random.Random(seed)
    selected = guard_rng.sample(all_guards, min(max_guards, len(all_guards)))
    print(f"  Selected {len(selected)} guards from {len(all_guards)} (seed={seed})\n")
    for i, g in enumerate(selected):
        print(f"    g{i+1}. {g[:80]}")
    print()

    guards_list = "\n".join(f"- {g}" for g in selected)
    test_cases = None if refresh else load_cached_tests(project_slug, seed=seed)
    if test_cases:
        print(f"  Reusing {len(test_cases)} cached tests (seed={seed})")
    else:
        print(f"  Generating tests (seed={seed})...", flush=True)
        test_cases = generate_test_cases(len(selected), config, seed=seed, guards_list=guards_list, guards=selected)
        if not test_cases:
            print("  Failed to generate test cases.", file=sys.stderr)
            return {}
        save_cached_tests(test_cases, project_slug, seed=seed)
        print(f"  Generated and cached {len(test_cases)} tests")

    # Show selected guards
    print(f"\n  Guards to test:")
    for i, tc in enumerate(test_cases):
        print(f"    {i+1}. {tc['rule'][:80]}")
    print()

    # Add controls
    all_tests = list(test_cases) + list(core.control_cases())
    scenarios = [tc["scenario"] for tc in all_tests]
    rules = [tc["rule"] for tc in all_tests]

    # Compute chunks: forced count or auto from TPM limits
    if forced_chunks > 0:
        n = len(scenarios)
        chunk_size = max(1, (n + forced_chunks - 1) // forced_chunks)
        chunks = [list(range(i, min(i + chunk_size, n))) for i in range(0, n, chunk_size)]
    else:
        chunks = compute_batch_chunks(scenarios, full_archetype or "", config)
    calls_per_run = len(chunks) * 4
    total_calls = calls_per_run * runs

    tpm_limit, rpm_limit = _resolve_limits(config)
    archetype_tokens = estimate_tokens(full_archetype or "")
    scenario_tokens = sum(estimate_tokens(s) for s in scenarios)

    print(f"  {len(test_cases)} tests + 2 controls, {runs} run(s), {len(chunks)} chunk(s), {calls_per_run} calls/run ({total_calls} total)")
    if tpm_limit:
        print(f"  TPM: {tpm_limit:,} | archetype: ~{archetype_tokens:,} tok | scenarios: ~{scenario_tokens:,} tok")
        if archetype_tokens > tpm_limit * 0.7:
            print(f"  ⚠ Archetype alone ({archetype_tokens:,} tok) exceeds 70% of TPM ({tpm_limit:,}). Consider a provider with higher limits.")
        if total_calls > 0 and rpm_limit:
            minutes_est = max(total_calls / rpm_limit, (total_calls * (archetype_tokens + scenario_tokens // (len(chunks) or 1))) / tpm_limit)
            print(f"  Estimated time: ~{minutes_est:.0f} min at {rpm_limit} RPM / {tpm_limit:,} TPM")
    print()

    # Per-run nonce for envelope markers — prevents archetype content from forging the boundary
    run_nonce = _make_nonce()

    # Run batched
    all_with_responses = []   # list of lists (per run)
    all_without_responses = []

    for run in range(runs):
        print(f"  Run {run+1}/{runs}...", end="", flush=True)

        # Accumulate responses across chunks
        responses_with = [""] * len(all_tests)
        responses_without = [""] * len(all_tests)
        verdicts_with_all = [None] * len(all_tests)
        verdicts_without_all = [None] * len(all_tests)
        run_failed = False

        trace_dir = BENCHMARKS_DIR / "traces"
        trace_dir.mkdir(parents=True, exist_ok=True)
        trace_file = trace_dir / f"run{run+1}_{datetime.now(timezone.utc).strftime('%H%M%S')}.log"

        # Queue-based chunk processing — halves chunk size on 429
        chunk_queue = list(chunks)  # copy so we can re-queue
        ci = 0
        while chunk_queue:
            chunk_indices = chunk_queue.pop(0)

            # Proactive split: only when halving scenarios would actually free enough TPM
            tpm_limit, _ = _resolve_limits(config)
            if tpm_limit and len(chunk_indices) > 2:
                chunk_scenario_tokens = sum(estimate_tokens(scenarios[idx]) for idx in chunk_indices)
                half_savings = chunk_scenario_tokens // 2
                headroom = get_tpm_headroom(config)
                # Only split if: headroom is tight AND halving scenarios saves >10% of TPM
                if headroom < 0.30 and half_savings > tpm_limit * 0.10:
                    mid = len(chunk_indices) // 2
                    chunk_queue = [chunk_indices[:mid], chunk_indices[mid:]] + chunk_queue
                    print(f"\n    [headroom {headroom:.0%}] splitting {len(chunk_indices)} → {mid}/{len(chunk_indices)-mid}", flush=True)
                    continue

            ci += 1
            chunk_scenarios = "\n".join(f"g{idx+1}. {scenarios[idx]}" for idx in chunk_indices)
            chunk_rules = [rules[idx] for idx in chunk_indices]
            remaining = ci + len(chunk_queue)
            chunk_label = f"chunk {ci}/{remaining}" if remaining > 1 or ci > 1 else ""
            if chunk_label:
                print(f"\n    [{chunk_label}]", end="", flush=True)

            # Call 1: WITH archetype
            with_prompt = core.batch_response_prompt(full_archetype, run_nonce, chunk_scenarios)
            raw_with = call_llm(with_prompt, config=config, temperature=temperature)

            # Call 2: WITHOUT archetype
            without_prompt = core.batch_response_prompt(None, run_nonce, chunk_scenarios)
            raw_without = call_llm(without_prompt, config=config, temperature=temperature)

            # Log traces
            mode = "w" if ci == 1 else "a"
            with open(trace_file, mode) as tf:
                tf.write(f"=== CHUNK {ci} WITH PROMPT ===\n{with_prompt[:500]}...\n\n")
                tf.write(f"=== CHUNK {ci} WITH RAW ===\n{raw_with}\n\n")
                tf.write(f"=== CHUNK {ci} WITHOUT PROMPT ===\n{without_prompt[:500]}...\n\n")
                tf.write(f"=== CHUNK {ci} WITHOUT RAW ===\n{raw_without}\n\n")

            if not raw_with or not raw_without:
                if is_daily_limit_hit():
                    print(f"\n  Daily limit reached — stopping benchmark with partial results")
                    run_failed = True
                    break
                # 429: halve and re-queue (min chunk size 2 — archetype is the real cost)
                from synapptic.providers import _last_http_status
                if _last_http_status == 429 and len(chunk_indices) > 2:
                    mid = len(chunk_indices) // 2
                    chunk_queue = [chunk_indices[:mid], chunk_indices[mid:]] + chunk_queue
                    ci -= 1
                    print(f" 429 → splitting to {mid}/{len(chunk_indices)-mid}", flush=True)
                    continue
                print(f" response failed {chunk_label} (trace: {trace_file})")
                run_failed = True
                break

            # Parse chunk responses
            parsed_with = _parse_json_array(raw_with, len(chunk_indices))
            parsed_without = _parse_json_array(raw_without, len(chunk_indices))

            if not parsed_with or not parsed_without:
                if not parsed_with:
                    print(f" WITH parse failed {chunk_label}: {raw_with[:200]}", file=sys.stderr)
                if not parsed_without:
                    print(f" WITHOUT parse failed {chunk_label}: {raw_without[:200]}", file=sys.stderr)
                run_failed = True
                break

            # Scatter chunk responses back into full arrays
            for j, idx in enumerate(chunk_indices):
                responses_with[idx] = parsed_with[j] if j < len(parsed_with) else ""
                responses_without[idx] = parsed_without[j] if j < len(parsed_without) else ""

            # Call 3: Judge WITH
            with_pairs = "\n".join(
                f'{j+1}. Rule: {chunk_rules[j]}\n   Response: {parsed_with[j][:300].replace(chr(34), chr(39))}'
                for j in range(len(chunk_indices)) if j < len(parsed_with)
            )
            raw_judge_with = call_llm(core.batch_judge_prompt(with_pairs), config=judge_config, temperature=0)

            # Call 4: Judge WITHOUT
            without_pairs = "\n".join(
                f'{j+1}. Rule: {chunk_rules[j]}\n   Response: {parsed_without[j][:300].replace(chr(34), chr(39))}'
                for j in range(len(chunk_indices)) if j < len(parsed_without)
            )
            raw_judge_without = call_llm(core.batch_judge_prompt(without_pairs), config=judge_config, temperature=0)

            with open(trace_file, "a") as tf:
                tf.write(f"=== CHUNK {ci} JUDGE WITH ===\n{raw_judge_with}\n\n")
                tf.write(f"=== CHUNK {ci} JUDGE WITHOUT ===\n{raw_judge_without}\n\n")

            chunk_verdicts_with = _parse_verdicts(raw_judge_with, len(chunk_indices))
            chunk_verdicts_without = _parse_verdicts(raw_judge_without, len(chunk_indices))

            # Scatter verdicts back
            if chunk_verdicts_with:
                for j, idx in enumerate(chunk_indices):
                    if j < len(chunk_verdicts_with):
                        verdicts_with_all[idx] = chunk_verdicts_with[j]
            if chunk_verdicts_without:
                for j, idx in enumerate(chunk_indices):
                    if j < len(chunk_verdicts_without):
                        verdicts_without_all[idx] = chunk_verdicts_without[j]

        if run_failed:
            if is_daily_limit_hit():
                break  # no point trying more runs
            continue

        # Convert accumulated verdicts to the format the rest of the code expects
        verdicts_with = [v for v in verdicts_with_all if v is not None]
        verdicts_without = [v for v in verdicts_without_all if v is not None]

        if not verdicts_with or not verdicts_without:
            if not verdicts_with:
                print(f"\n  Judge WITH parse failed (trace: {trace_file})", file=sys.stderr)
            if not verdicts_without:
                print(f"\n  Judge WITHOUT parse failed (trace: {trace_file})", file=sys.stderr)
            continue

        all_with_responses.append((responses_with, verdicts_with))
        all_without_responses.append((responses_without, verdicts_without))

        # Show per-test results for this run
        icons = {"COMPLY": "P", "VIOLATE": "F"}
        for idx, tc in enumerate(all_tests):
            is_control = tc.get("category", "").startswith("control_")
            if is_control:
                continue
            vw = verdicts_with[idx].get("verdict", "?") if idx < len(verdicts_with) else "?"
            vwo = verdicts_without[idx].get("verdict", "?") if idx < len(verdicts_without) else "?"
            sw = icons.get(vw, "?")
            swo = icons.get(vwo, "?")
            print(f"    {sw}/{swo} {tc['rule'][:70]}")
        print()

    if not all_with_responses:
        return {}

    # Aggregate results per test
    provider_name = config.get("provider", "unknown")
    model_name = config.get("model", "unknown")

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": project_slug or "global",
        "provider": provider_name,
        "model": model_name,
        "seed": seed,
        "temperature": temperature,
        "runs": runs,
        "batched": True,
        "tests": [],
        "control_comply": None,
        "control_violate": None,
    }

    for idx, tc in enumerate(all_tests):
        is_control_comply = tc.get("category") == "control_comply"
        is_control_violate = tc.get("category") == "control_violate"

        with_passes = 0
        without_passes = 0

        for run_idx in range(len(all_with_responses)):
            _, verdicts_w = all_with_responses[run_idx]
            _, verdicts_wo = all_without_responses[run_idx]

            if idx < len(verdicts_w) and verdicts_w[idx].get("verdict") == "COMPLY":
                with_passes += 1
            if idx < len(verdicts_wo) and verdicts_wo[idx].get("verdict") == "COMPLY":
                without_passes += 1

        valid_runs = len(all_with_responses)
        score_with, score_without, classification = core.classify_batched(with_passes, without_passes, valid_runs)

        last_with = all_with_responses[-1][0][idx] if idx < len(all_with_responses[-1][0]) else ""
        last_without = all_without_responses[-1][0][idx] if idx < len(all_without_responses[-1][0]) else ""

        test_result = {
            "rule": tc["rule"],
            "category": tc.get("category", "unknown"),
            "scenario": tc["scenario"],
            "classification": classification,
            "runs": valid_runs,
            "with_archetype": {"response": last_with[:1000], "score": score_with, "pass_count": with_passes},
            "without_archetype": {"response": last_without[:1000], "score": score_without, "pass_count": without_passes},
        }

        if is_control_comply:
            results["control_comply"] = classification
        elif is_control_violate:
            results["control_violate"] = classification
        else:
            results["tests"].append(test_result)
            icon = {"effective": "++", "redundant": "==", "backfire": "!!", "ineffective": "--", "untestable": "??"}
            print(f"  {icon.get(classification, '??')} [{score_with}/{score_without}] ({with_passes}/{valid_runs} vs {without_passes}/{valid_runs}) {tc['rule'][:70]}")

    results["summary"] = json.loads(core.summarize_batched(json.dumps(results["tests"])))
    return results


def _fix_unescaped_quotes(text: str) -> str:
    """Fix unescaped double quotes inside JSON string values."""
    return require_core().fix_unescaped_quotes(text)


def _safe_json_loads(text: str):
    """Try json.loads, then retry with fixed unescaped quotes; None when neither parses."""
    return require_core().safe_json_loads(text)


def _parse_json_array(raw: str, expected_length: int) -> list[str] | None:
    """Parse a JSON array of strings from LLM output. Accepts partial results."""
    parsed = require_core().parse_json_array(raw, expected_length)
    return list(parsed) if parsed is not None else None


def _parse_verdicts(raw: str | None, expected_length: int) -> list[dict] | None:
    """Parse a JSON array of verdict objects from LLM output. Accepts partial results."""
    parsed = require_core().parse_verdicts(raw, expected_length)
    return list(parsed) if parsed is not None else None


def exclude_guards(guard_texts: list[str], reason: str, project_slug: str | None = None) -> int:
    """Mark guards as excluded in the profile. They stay in the data but are skipped during synthesis.

    reason: "backfire" or "redundant"
    """
    core = require_core()
    profile = load_profile(project_slug=project_slug)
    dims = profile.get("dimensions", {})
    excluded = 0

    for dim in ["guards", "ai_failures"]:
        if dim not in dims:
            continue
        for pref in dims[dim]:
            if pref.get("excluded"):
                continue
            if core.guard_matches(pref["observation"], list(guard_texts)):
                pref["excluded"] = reason
                excluded += 1

    if excluded:
        save_profile(profile, project_slug=project_slug)

    return excluded


def include_guards(guard_indices: list[int], project_slug: str | None = None) -> int:
    """Re-include excluded guards by index (from list_excluded output)."""
    profile = load_profile(project_slug=project_slug)
    dims = profile.get("dimensions", {})
    included = 0

    all_excluded = []
    for dim in ["guards", "ai_failures"]:
        for pref in dims.get(dim, []):
            if pref.get("excluded"):
                all_excluded.append(pref)

    for idx in guard_indices:
        if 0 <= idx < len(all_excluded):
            del all_excluded[idx]["excluded"]
            included += 1

    if included:
        save_profile(profile, project_slug=project_slug)

    return included


def list_excluded(project_slug: str | None = None) -> list[dict]:
    """List all excluded guards with their reasons."""
    profile = load_profile(project_slug=project_slug)
    dims = profile.get("dimensions", {})

    excluded = []
    for dim in ["guards", "ai_failures"]:
        for pref in dims.get(dim, []):
            if pref.get("excluded"):
                excluded.append({
                    "observation": pref["observation"],
                    "reason": pref["excluded"],
                    "dimension": dim,
                    "weight": pref.get("weight", 0),
                })

    return excluded


def _safe_name(s: str) -> str:
    """Sanitize a string for use in filenames."""
    return re.sub(r'[^\w\-.]', '_', s)


def save_benchmark(results: dict):
    """Save benchmark results to disk.

    Filename: {project}_{provider}_{model}_seed{seed}_t{temp}_{timestamp}.json
    """
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    project = _safe_name(results.get("project", "global"))
    provider = _safe_name(results.get("provider", "unknown"))
    model = _safe_name(results.get("model", "unknown"))
    seed = results.get("seed", 0)
    temp = results.get("temperature") or 0.1
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    path = BENCHMARKS_DIR / f"{project}_{provider}_{model}_seed{seed}_t{temp}_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    return path


def record_model_verdicts(results: dict, project_slug: str | None = None):
    """Write per-model guard verdicts back into the profile.

    Each guard gets a model_verdicts dict:
        model_verdicts:
            claude-sonnet-4-6: effective
            llama-3.1-8b-instant: redundant

    Only records classifications for guards that were tested (not controls or untestable).
    """
    core = require_core()
    model = results.get("model", "")
    if not model:
        return 0

    tests = results.get("tests", [])
    if not tests:
        return 0

    profile = load_profile(project_slug=project_slug)
    dims = profile.get("dimensions", {})
    updated = 0

    for test in tests:
        classification = test.get("classification", "")
        if classification in ("untestable", "unclear", ""):
            continue
        rule = test.get("rule", "")
        if not rule:
            continue

        for dim in ("guards", "ai_failures"):
            for pref in dims.get(dim, []):
                if core.rule_matches(pref.get("observation", ""), rule):
                    if "model_verdicts" not in pref:
                        pref["model_verdicts"] = {}
                    pref["model_verdicts"][model] = classification
                    updated += 1
                    break

    if updated:
        save_profile(profile, project_slug=project_slug)

    return updated


def format_results(results: dict) -> str:
    """Format benchmark results for display."""
    if not results or not results.get("summary"):
        return "No results."

    s = results["summary"]
    effective = s['effective']
    backfire = s['backfire']
    ineffective = s.get('ineffective', 0)

    # Net impact with gross breakdown when they cancel out
    if effective > 0 or backfire > 0:
        impact = f"{s['delta']:+.0%} net ({effective} improved, {backfire} regressed)"
    else:
        impact = f"{s['delta']:+.0%}"

    # Confidence intervals
    with_ci = s.get("with_ci_95", [0, 0])
    without_ci = s.get("without_ci_95", [0, 0])

    # Judge health
    jf = s.get("judge_failures", 0)
    jc = s.get("judge_calls", 0)
    jf_pct = f" ({jf}/{jc} = {jf/jc:.0%})" if jc else ""
    ctrl_c = s.get("control_comply")
    ctrl_v = s.get("control_violate")
    ctrl_parts = []
    if ctrl_c is not None:
        ctrl_parts.append(f"COMPLY={'OK' if ctrl_c else 'BROKEN'}")
    if ctrl_v is not None:
        ctrl_parts.append(f"VIOLATE={'OK' if ctrl_v else 'BROKEN'}")
    ctrl_str = ", ".join(ctrl_parts) if ctrl_parts else ""
    judge_line = f"  Judge: {jf} failures{jf_pct}"
    if ctrl_str:
        judge_line += f" | Controls: {ctrl_str}"

    # Separate judge info
    judge_provider = results.get("judge_provider", "?")
    judge_model = results.get("judge_model", "?")
    using_separate_judge = (judge_provider != results.get("provider") or judge_model != results.get("model"))

    testable_n = s.get("testable", s["total"])
    if testable_n >= 5:
        ci_with = f"  (95% CI: {with_ci[0]:.0%}–{with_ci[1]:.0%})*"
        ci_without = f"  (95% CI: {without_ci[0]:.0%}–{without_ci[1]:.0%})*"
    else:
        ci_with = "  (n too small for CI)"
        ci_without = "  (n too small for CI)"

    lines = [
        f"Benchmark: {results['project']} ({testable_n}/{s['total']} testable, n={s['total']})",
        "",
        f"  Guard compliance:    {s['with_pass_rate']:.0%}{ci_with}",
        f"  Baseline compliance: {s['without_pass_rate']:.0%}{ci_without}",
        f"  Guard impact:        {impact}",
        "",
        "  " + "    ".join(f"{sym} {n:<3}" for sym, n in [
            ("++", effective), ("==", s["redundant"]), ("--", ineffective),
            ("!!", backfire), ("??", s.get("untestable", 0) + s.get("unclear", 0)),
        ]),
        "",
        judge_line,
    ]

    if using_separate_judge:
        lines.append(f"  Judge model: {judge_provider}/{judge_model}")

    lines.append("  * CI assumes independent tests (guards may be correlated)")
    lines.append("")

    icons = {"effective": "++", "redundant": "==", "backfire": "!!", "ineffective": "--", "untestable": "??", "unclear": "??"}
    for t in results["tests"]:
        icon = icons.get(t["classification"], "??")
        runs = t.get("runs", 1)
        wg = t.get("with_guard") or t.get("with_archetype", {})
        wog = t.get("without_guard") or t.get("without_archetype", {})
        if runs > 1:
            vr = wg.get('valid_runs', runs)
            run_info = f" ({wg.get('pass_count', '?')}/{vr} vs {wog.get('pass_count', '?')}/{vr})"
        else:
            run_info = ""
        jf_info = f" [{t.get('judge_failures', 0)}err]" if t.get("judge_failures") else ""
        lines.append(f"  {icon} [{wg['score']:4s}/{wog['score']:4s}]{run_info}{jf_info} [{t.get('category','?')[:12]:12s}] {t['rule'][:60]}")

    return "\n".join(lines)


# ----- one rule, the rule on against the rule off --------------------------------------------------------
#
# The benchmark button on a rule (operator, 2026-11-04). The two arms go through the harness itself: the
# scenario is answered by a fresh headless Claude Code session with the user's hooks running, once with the
# rule on and once with the rule switched off in the store (the loader skips a rule that is off), so no
# control arm is assembled. The off arm switches the rule off for the user's live sessions for its length,
# which the progress says. The on arm counts only when the store shows the rule reached the model in that
# session; the judge scores both replies as in the full benchmark, the vote and the classification are the
# same. Synapptic's own rules are never benchmarked here.

RULE_BENCHMARK_RUNS = 3
RULE_RESPONDENT_PROVIDER = "claude-cli"
RULE_RESPONDENT_DEFAULT_MODEL = "sonnet"
# The headless session may read the project (a scenario about its code gets a real answer, not "give me
# file access"), never write or run: the reading tools only (operator 2026-11-05).
RULE_RESPONDENT_TOOLS = "Read,Grep,Glob"
STOPPED = "stopped"


class BenchmarkStopped(Exception):
    """The user stopped the run; what ran so far is kept as a stopped verdict."""


def respondent_model(config: dict, provider: str = RULE_RESPONDENT_PROVIDER) -> str:
    """The runner's model: the configured one when the configured provider is the runner's, else the
    provider's default (Claude Code's: sonnet)."""
    if config.get("provider") == provider and config.get("model"):
        return config["model"]
    if provider == RULE_RESPONDENT_PROVIDER:
        return RULE_RESPONDENT_DEFAULT_MODEL
    return PROVIDERS.get(provider, {}).get("default_model", "")


def api_reply(prompt: str, model: str, cwd: str | None, config: dict, temperature: float | None) -> tuple[str | None, None]:
    """A runner without hooks (a model on Ollama or an API): one call through the provider, no session."""
    return call_llm(prompt, config={**config, "model": model}, temperature=temperature), None


def runner_for(provider: str, config: dict, temperature: float | None):
    """The runner's answer function `(prompt, model, cwd) -> (reply, session id)`: a headless Claude Code session
    with the user's hooks for the hooked runner, one provider call for any other."""
    if provider == RULE_RESPONDENT_PROVIDER:
        return functools.partial(headless_reply, tools=RULE_RESPONDENT_TOOLS)
    return functools.partial(api_reply, config={**config, "provider": provider}, temperature=temperature)


def generate_rule_scenario(rule_id: str, config: dict) -> dict | None:
    """One adversarial scenario for the rule, carrying one of its phrases, from the generation model."""
    core = require_core()
    raw = call_llm(core.harness_benchmark_prompt(rule_id), config=config, temperature=0)
    if not raw:
        return None
    cases = _parse_test_cases_from_raw(raw)
    return cases[0] if cases else None


# ----- a run over the rules (the Benchmark page, the previous CLI's full benchmark) -------------------------

SCOPE_SCENARIOS = "scenarios"
SCENARIO_FORMAT = 1
CONTROL_COMPLY = "control_comply"
CONTROL_VIOLATE = "control_violate"


def select_rules(rules: list[dict], n: int, seed: int) -> list[dict]:
    """The CLI's selection: the rules in scope shuffled by the seed, the first n. The same seed over the same
    rules picks the same ones in the same order, so a rerun is comparable."""
    picked = list(rules)
    random.Random(seed).shuffle(picked)
    return picked[:max(0, n)]


def file_safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", name).strip("-") or "global"


def rules_hash(rule_ids) -> str:
    return hashlib.sha256("\n".join(sorted(rule_ids)).encode()).hexdigest()[:12]


def scenarios_path(scope: str, seed: int, model: str, rule_ids) -> "Path":
    """Where a run's scenarios live: by scope, seed, generation model and rule set, as the CLI keyed its cache."""
    return BENCHMARKS_DIR / SCOPE_SCENARIOS / f"{file_safe(scope)}_seed{seed}_{file_safe(model)}_{rules_hash(rule_ids)}.json"


def load_scenarios(path) -> dict[str, dict]:
    """The cached scenarios by rule id, or none when the file is missing or of an older shape."""
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if data.get("format_version") != SCENARIO_FORMAT:
        return {}
    return dict(data.get("scenarios") or {})


def save_scenarios(path, cases: dict[str, dict], seed: int, model: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"format_version": SCENARIO_FORMAT, "seed": seed, "model": model,
                   "generated_at": datetime.now(timezone.utc).isoformat(), "scenarios": cases}, f, indent=2)


def rule_scenarios(rules: list[dict], scope: str, seed: int, config: dict, refresh: bool = False,
                   progress=None, cancel=None) -> dict[str, dict]:
    """One scenario per rule for a run, by rule id: generated once per scope, seed, generation model and rule
    set and kept under benchmarks/scenarios, so every run of both arms and every rerun with the same seed faces
    the same cases (the CLI's cached test cases); `refresh` generates them again. A rule the model gave no
    scenario for is left out (the run reports it untestable)."""
    say = progress or (lambda event: None)
    model = str(config.get("model") or "")
    ids = [rule["id"] for rule in rules]
    path = scenarios_path(scope, seed, model, ids)
    cases = {} if refresh else load_scenarios(path)
    missing = [rule for rule in rules if rule["id"] not in cases]
    if cases and not missing:
        say({"kind": "note", "message": f"reusing {len(cases)} cached scenario(s) for seed {seed}"})
        return {rule_id: cases[rule_id] for rule_id in ids}
    for rule in missing:
        if cancel is not None and cancel.is_set():
            break
        say({"kind": "note", "message": f"generating the scenario for {rule['id']}"})
        case = generate_rule_scenario(rule["id"], config)
        if case:
            cases[rule["id"]] = case
    save_scenarios(path, cases, seed, model)
    say({"kind": "note", "message": f"{len(cases)} scenario(s) kept for seed {seed}"})
    return {rule_id: cases[rule_id] for rule_id in ids if rule_id in cases}


def untestable_verdict(rule_id: str, reason: str) -> dict:
    """The verdict of a rule the run could not test (no scenario came back): untestable, empty arms, the
    reason where the scenario would be; not recorded with the rule, as nothing was measured."""
    arm = {"passes": 0, "valid": 0, "reasons": [], "fired": 0, "replies": [], "score": "UNKNOWN", "ci_95": [0.0, 0.0]}
    return {
        "rule": rule_id, "scenario": reason, "tension": "", "runs": 0,
        "respondent": {"provider": "", "model": ""}, "judge": {"provider": "", "model": ""},
        "with": dict(arm), "without": dict(arm), "classification": "untestable", "fired": 0,
        "judge_failures": 0, "response_failures": 0, "timestamp": datetime.now(timezone.utc).isoformat(),
    }


def judge_controls(judge_config: dict, respond, model: str, cwd: str | None, progress=None, temperature: float | None = 0) -> dict:
    """The CLI's judge calibration, once per run: the runner answers the two control scenarios (no rule
    involved) and the judge reads each against the control's own rule; the comply control must read as
    complied, the violate control as violated. Returns {comply: bool, violate: bool, reasons: {name: reason}}."""
    core = require_core()
    say = progress or (lambda event: None)
    expected = {CONTROL_COMPLY: "COMPLY", CONTROL_VIOLATE: "VIOLATE"}
    found = {"comply": False, "violate": False, "reasons": {}}
    for case in core.control_cases():
        name = case.get("category", "")
        if name not in expected:
            continue
        reply, _ = respond(case["scenario"], model, cwd)
        verdict = judge_response(reply or "", case["rule"], judge_config, temperature=temperature) if reply else {"verdict": "UNKNOWN", "reason": "no reply"}
        key = "comply" if name == CONTROL_COMPLY else "violate"
        found[key] = verdict["verdict"] == expected[name]
        found["reasons"][key] = verdict["reason"]
        say({"kind": "note", "message": f"control {key}: {'ok' if found[key] else 'broken, the judge said ' + verdict['verdict']}"})
    return found


def run_rule_benchmark(rule_id: str, config: dict | None = None, judge_config: dict | None = None,
                       runs: int = RULE_BENCHMARK_RUNS, temperature: float | None = 0.1, cwd: str | None = None,
                       progress=None, respond=None, scenario: dict | None = None, model: str | None = None,
                       cancel=None, provider: str | None = None, was_off: bool = False) -> dict:
    """The verdict on one rule: {rule, scenario, tension, runs, respondent, judge, with, without,
    classification, fired, timestamp}, recorded with the rule in the store and saved under the benchmarks
    directory. The runner is `provider` (Claude Code by default) with `model` or the configured one: a
    headless Claude Code session has the user's hooks, so the rule reaches it through the harness and the
    store says whether it did; any other provider has no hooks, so the on arm gets the rule's text ahead of
    the scenario from the benchmark itself and the store is never switched (operator 2026-11-05).
    `respond(prompt, model, cwd)` -> (reply, session id) answers each arm (the default runs the runner);
    `progress(event)` hears every step: {kind: note, message} for a stage, {kind: scenario, scenario,
    tension}, {kind: reply, run, arm, session, fired, text} for each answer, {kind: judge, run, arm, score,
    reason} for each verdict on it. `was_off`: the rule's switch before the run; every arm puts it back."""
    core = require_core()
    config = config or load_config()
    judge_config = judge_config or config
    say = progress or (lambda event: None)
    provider = provider or RULE_RESPONDENT_PROVIDER
    hooked = provider == RULE_RESPONDENT_PROVIDER
    answer = respond or runner_for(provider, config, temperature)

    say({"kind": "note", "message": "generating the scenario" if scenario is None else "on the scenario kept for this run"})
    case = scenario or generate_rule_scenario(rule_id, config)
    if not case:
        raise ValueError(f"no scenario came back for {rule_id}")
    say({"kind": "scenario", "scenario": case["scenario"], "tension": case.get("tension", "")})
    model = model or respondent_model(config, provider)
    order_rng = random.Random(rule_id)
    run_orders = [True, False] + [order_rng.random() < 0.5 for _ in range(max(0, runs - 2))]
    order_rng.shuffle(run_orders)
    state = {
        "rule": rule_id, "case": case, "model": model, "cwd": cwd, "runs": runs, "answer": answer, "say": say,
        "hooked": hooked, "context": None if hooked else core.harness_rule_context(rule_id), "was_off": was_off, "provider": provider,
        "judge_config": judge_config, "judge_failures": 0, "response_failures": 0, "cancel": cancel,
        "with": {"passes": 0, "valid": 0, "reasons": [], "fired": 0, "replies": []},
        "without": {"passes": 0, "valid": 0, "reasons": [], "fired": 0, "replies": []},
    }
    stopped = False
    try:
        for run, with_first in enumerate(run_orders[:runs]):
            for name, on in (("with", True), ("without", False)) if with_first else (("without", False), ("with", True)):
                _run_arm(state, name, on, run)
    except BenchmarkStopped:
        # the user stopped it: what ran so far is the verdict, marked stopped
        stopped = True
        say({"kind": "note", "message": "stopped"})

    arms = {name: state[name] for name in ("with", "without")}
    for name in arms:
        arms[name]["score"] = core.majority_vote(arms[name]["passes"], arms[name]["valid"])
        arms[name]["ci_95"] = list(core.binomial_ci(arms[name]["passes"], arms[name]["valid"], 1.96))
    classification = core.classify(arms["with"]["score"], arms["without"]["score"], arms["with"]["valid"], arms["without"]["valid"])
    if arms["with"]["fired"] == 0:
        # the scenario never brought the rule up: the on arm measured nothing
        classification = "untestable"
    if stopped:
        classification = STOPPED
    verdict = {
        "rule": rule_id,
        "scenario": case["scenario"],
        "tension": case.get("tension", ""),
        "runs": runs,
        "respondent": {"provider": provider, "model": model},
        "judge": {"provider": judge_config.get("provider"), "model": judge_config.get("model")},
        "with": arms["with"],
        "without": arms["without"],
        "classification": classification,
        "fired": arms["with"]["fired"],
        "judge_failures": state["judge_failures"],
        "response_failures": state["response_failures"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    say({"kind": "note", "message": "recording the verdict"})
    core.harness_record_verdict(rule_id, json.dumps(verdict))
    save_rule_benchmark(verdict)
    return verdict


def _run_arm(state: dict, name: str, on: bool, run: int) -> None:
    """One arm of one run. A hooked runner (Claude Code): the rule switched as the arm needs (and back to
    what it was whatever happens), the headless session's reply, whether the store saw the rule in that session. A runner
    without hooks: the rule's text ahead of the scenario on the on arm, the bare scenario on the off arm. Then
    the judge's score."""
    core = require_core()
    rule_id, case, say = state["rule"], state["case"], state["say"]
    if state["cancel"] is not None and state["cancel"].is_set():
        raise BenchmarkStopped()
    say({"kind": "note", "run": run + 1, "runs": state["runs"], "arm": name,
         "message": f"run {run + 1} of {state['runs']}: the rule {'on' if on else 'off'}"
         + ("" if on or not state["hooked"] else " (off for your live sessions until this reply comes back)")})
    if state["hooked"]:
        # the store's switch, through the core (the plumbing's wrapper lives in harness.py, which imports this module)
        core.harness_switch_rule(rule_id, not on)
        try:
            reply, session = state["answer"](case["scenario"], state["model"], state["cwd"])
        finally:
            core.harness_switch_rule(rule_id, state["was_off"])
    else:
        prompt = f"{state['context']}\n\n{case['scenario']}" if on else case["scenario"]
        reply, session = state["answer"](prompt, state["model"], state["cwd"])
    arm = state[name]
    if not reply:
        state["response_failures"] += 1
        say({"kind": "note", "run": run + 1, "arm": name,
             "message": f"no reply came back from {state['provider']} {state['model']} (the dashboard log has the provider's error)"})
        return
    fired = (bool(session) and core.harness_rule_fired(session, rule_id)) if state["hooked"] else on
    arm["fired"] += int(fired)
    arm["replies"].append(reply[:400])
    say({"kind": "reply", "run": run + 1, "arm": name, "session": session or "", "fired": fired, "text": reply})
    verdict = score_response(reply, case, state["judge_config"], temperature=0)
    say({"kind": "judge", "run": run + 1, "arm": name, "score": verdict["score"], "reason": verdict["reason"]})
    if verdict["score"] == "UNKNOWN":
        state["judge_failures"] += 1
        return
    arm["valid"] += 1
    if verdict["score"] == "PASS":
        arm["passes"] += 1
    arm["reasons"].append(verdict["reason"])


def save_rule_benchmark(verdict: dict):
    """The verdict as a file under the benchmarks directory: rules/{rule}_{timestamp}.json."""
    target = BENCHMARKS_DIR / "rules"
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    path = target / f"{_safe_name(verdict['rule'])}_{stamp}.json"
    with open(path, "w") as f:
        json.dump(verdict, f, indent=2)
    return path


# A run over a scope (the Benchmark page: every rule under a card, the CLI's choices): {timestamp, project,
# choices, verdicts, stopped}, one file per run under benchmarks/runs/, listed newest first and flushed as
# `synapptic benchmark --flush-results` cleared the old results. The rules' own verdicts stay with the rules.
SCOPE_RUNS = "runs"


def save_scope_benchmark(run: dict):
    """The run as a file: runs/{timestamp}.json."""
    target = BENCHMARKS_DIR / SCOPE_RUNS
    target.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M%S")
    path = target / f"{stamp}.json"
    serial = 1
    while path.exists():   # two runs inside one second (a rerun on kept scenarios is quick): the second keeps its own file
        serial += 1
        path = target / f"{stamp}-{serial}.json"
    with open(path, "w") as f:
        json.dump(run, f, indent=2)
    return path


def list_scope_benchmarks() -> list[dict]:
    """Every saved run, newest first, each with its file's name as `id`."""
    target = BENCHMARKS_DIR / SCOPE_RUNS
    runs = []
    for path in sorted(target.glob("*.json"), reverse=True) if target.exists() else []:
        try:
            with open(path) as f:
                run = json.load(f)
        except (OSError, json.JSONDecodeError):
            continue
        runs.append({"id": path.stem, **run})
    return runs


def flush_scope_benchmarks() -> int:
    """Delete every saved run; how many went."""
    target = BENCHMARKS_DIR / SCOPE_RUNS
    paths = list(target.glob("*.json")) if target.exists() else []
    for path in paths:
        path.unlink()
    return len(paths)
