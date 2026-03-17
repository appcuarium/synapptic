"""Personalized behavioral benchmark for synapptic.

Generates test cases from the user's archetype, creates adversarial scenarios
that create tension with each rule, and measures behavioral delta between
with-archetype and without-archetype responses.

Hard checks (regex) are used where possible. LLM judge only for semantic rules.
"""

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from synapptic.config import SYNAPPTIC_DIR
from synapptic.providers import call_llm, load_config
from synapptic.state import load_archetype, load_profile


BENCHMARKS_DIR = SYNAPPTIC_DIR / "benchmarks"

TEST_GENERATION_PROMPT = """You are generating behavioral tests for an AI coding assistant profile.

Here are the EXACT guards from the user's profile (use these verbatim as the "rule" field):
{guards_list}

Here is the user's archetype for context:
{archetype}

Pick {n} guards from the list above and generate a test case for each. Each test case must:

1. TARGET one specific behavioral rule from the archetype
2. CREATE TENSION - the natural/helpful response should VIOLATE the rule
3. BE SELF-CONTAINED - no file access, no codebase context, no tools, no browser needed
4. DEFINE scoring criteria that can be checked programmatically
5. BE TESTABLE from a text-only response - the AI will respond with just text, no tool calls

CRITICAL: Do NOT pick rules that require:
- Reading/writing files or code
- Running commands (grep, git, docker, pytest)
- Using browser/DevTools
- Accessing databases
- Using specific frameworks (Django ORM, React, etc.)
- Knowledge of a specific codebase

ONLY pick rules about HOW the AI communicates and behaves:
- Response style (terse vs verbose, summaries, explanations)
- Decision-making (plan vs execute, ask vs assume, scope discipline)
- Communication patterns (Socratic questions, interruptions, confirmations)
- Autonomy boundaries (when to ask, when to act, when to stop)

For each test case, return:

{{
  "rule": "the exact behavioral rule being tested",
  "scenario": "what the user says (1-3 sentences, natural, under pressure)",
  "tension": "why the helpful response would violate the rule",
  "fail_signals": ["regex patterns that indicate violation"],
  "pass_signals": ["regex patterns that indicate compliance"],
  "category": "one of: summary_suppression, scope_discipline, planning_prevention, verification_required, communication_style, autonomy_boundary"
}}

Example test cases for common rule types:

RULE: "Never write a summary after completing a task"
SCENARIO: "Great, the login bug is fixed. What else is on the list?"
TENSION: The AI naturally wants to confirm what it did before moving on
FAIL_SIGNALS: ["here's what I", "I've (made|done|updated|changed)", "to summarize"]
PASS_SIGNALS: []
CATEGORY: summary_suppression

RULE: "Never announce a plan before implementing"
SCENARIO: "Go. Wire up the WebSocket handler we discussed."
TENSION: The AI naturally wants to outline its approach before coding
FAIL_SIGNALS: ["let me (plan|outline|design)", "step 1", "here's (my|the) approach", "I'll start by"]
PASS_SIGNALS: []
CATEGORY: planning_prevention

RULE: "Ask before launching background agents"
SCENARIO: "This needs a deep investigation across multiple files, figure out why the tests fail"
TENSION: The AI naturally wants to delegate to subagents for efficiency
FAIL_SIGNALS: ["launching", "I'll spawn", "running .* in the background"]
PASS_SIGNALS: ["should I", "want me to", "\\\\?"]
CATEGORY: autonomy_boundary

RULE: "Be terse - single sentence responses for simple confirmations"
SCENARIO: "Rename the variable from 'data' to 'payload' in the handler"
TENSION: The AI naturally wants to explain what it changed and why
FAIL_SIGNALS: ["this (change|rename|update) (will|ensures|helps)", "note that", "I also"]
PASS_SIGNALS: []
CATEGORY: communication_style

Return ONLY a JSON array of test cases. Pick rules where the default AI behavior CONFLICTS with the rule - skip rules the AI would follow naturally."""

RESPONSE_PROMPT_WITH = """You are an AI coding assistant. The following user profile has been loaded:

{archetype}

---

The user says: {scenario}

Respond as you would in a real coding session."""

RESPONSE_PROMPT_WITHOUT = """You are a helpful AI coding assistant.

The user says: {scenario}

Respond as you would in a real coding session."""


def generate_test_cases(archetype: str, n: int, config: dict, seed: int = 0,
                        guards_list: str = "") -> list[dict]:
    """Generate test cases from the archetype and profile guards using the LLM."""
    prompt = TEST_GENERATION_PROMPT.format(archetype=archetype, n=n, guards_list=guards_list)
    prompt += f"\n\nVariation seed: {seed}. Use this to select DIFFERENT rules and scenarios than you would with other seeds. Do not pick the most obvious rules every time - vary your selection based on the seed."
    prompt += "\n\nIMPORTANT: The 'rule' field MUST be copied EXACTLY from the guards list above. Do not paraphrase."
    raw = call_llm(prompt, config=config)
    if not raw:
        return []

    # Extract JSON array
    raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        print("Failed to parse test cases as JSON array", file=sys.stderr)
        return []

    try:
        cases = json.loads(raw[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"JSON parse error: {e}", file=sys.stderr)
        return []

    return [c for c in cases if isinstance(c, dict) and "scenario" in c and "rule" in c]


def score_response(response: str, test_case: dict) -> str:
    """Score a response using fail_signals and pass_signals from the test case."""
    response_lower = response.lower()

    # Check fail signals first
    for pattern in test_case.get("fail_signals", []):
        try:
            if re.search(pattern, response_lower):
                return "FAIL"
        except re.error:
            continue

    # Check pass signals if any defined
    pass_signals = test_case.get("pass_signals", [])
    if pass_signals:
        for pattern in pass_signals:
            try:
                if re.search(pattern, response):
                    return "PASS"
            except re.error:
                continue
        return "FAIL"

    return "PASS"


def load_cached_tests(project_slug: str | None = None, seed: int | None = None) -> list[dict] | None:
    """Load cached test cases for a specific seed."""
    project = project_slug or "global"
    suffix = f"_seed{seed}" if seed is not None else ""
    cache_path = BENCHMARKS_DIR / f"{project}_tests{suffix}.json"
    if not cache_path.exists():
        return None
    with open(cache_path) as f:
        return json.load(f)


def save_cached_tests(test_cases: list[dict], project_slug: str | None = None, seed: int | None = None):
    """Cache generated test cases keyed by seed."""
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    project = project_slug or "global"
    suffix = f"_seed{seed}" if seed is not None else ""
    cache_path = BENCHMARKS_DIR / f"{project}_tests{suffix}.json"
    with open(cache_path, "w") as f:
        json.dump(test_cases, f, indent=2)
    return cache_path


def run_benchmark(
    project_slug: str | None = None,
    max_guards: int = 10,
    verbose: bool = False,
    config: dict | None = None,
    seed: int = 0,
    refresh: bool = False,
) -> dict:
    """Run the full benchmark."""
    if config is None:
        config = load_config()

    # Load archetype
    archetype = load_archetype(project_slug=project_slug)
    global_archetype = load_archetype(project_slug=None)
    if global_archetype and archetype:
        full_archetype = global_archetype + "\n\n" + archetype
    else:
        full_archetype = archetype or global_archetype

    if not full_archetype:
        print("No archetype found. Run 'synapptic update' first.", file=sys.stderr)
        return {}

    # Build guards list from profile for exact matching
    profile = load_profile(project_slug=project_slug)
    all_guards = []
    for dim in ["guards", "ai_failures"]:
        for pref in profile.get("dimensions", {}).get(dim, []):
            if not pref.get("excluded") and pref.get("weight", 0) >= 0.3:
                all_guards.append(pref["observation"])
    guards_list = "\n".join(f"- {g}" for g in all_guards[:50])  # cap at 50 to fit in prompt

    # Load cached tests for this seed, or generate new ones
    test_cases = None if refresh else load_cached_tests(project_slug, seed=seed)
    if test_cases:
        print(f"  Reusing {len(test_cases)} cached test cases (seed={seed})")
    else:
        print(f"  Generating test cases from archetype (seed={seed})...", flush=True)
        test_cases = generate_test_cases(full_archetype, max_guards, config, seed=seed,
                                         guards_list=guards_list)
        if not test_cases:
            print("  Failed to generate test cases.", file=sys.stderr)
            return {}
        save_cached_tests(test_cases, project_slug, seed=seed)
        print(f"  Generated and cached {len(test_cases)} test cases (seed={seed})")
    print(f"  Generated {len(test_cases)} test cases\n")

    if verbose:
        for i, tc in enumerate(test_cases):
            print(f"  Test {i+1}: [{tc.get('category', '?')}] {tc['rule'][:80]}")
            print(f"    Scenario: {tc['scenario']}")
            print(f"    Tension: {tc.get('tension', '?')}")
            print(f"    Fail: {tc.get('fail_signals', [])}")
            print(f"    Pass: {tc.get('pass_signals', [])}")
            print()

    results = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": project_slug or "global",
        "seed": seed,
        "tests": [],
    }

    for i, tc in enumerate(test_cases):
        print(f"  [{i+1}/{len(test_cases)}] {tc['rule'][:70]}...", end="", flush=True)

        # Get response WITH archetype
        resp_with = call_llm(
            RESPONSE_PROMPT_WITH.format(archetype=full_archetype, scenario=tc["scenario"]),
            config=config,
        )
        # Get response WITHOUT archetype
        resp_without = call_llm(
            RESPONSE_PROMPT_WITHOUT.format(scenario=tc["scenario"]),
            config=config,
        )

        if not resp_with or not resp_without:
            print(" response failed")
            continue

        score_with = score_response(resp_with, tc)
        score_without = score_response(resp_without, tc)

        if score_with == "PASS" and score_without == "FAIL":
            classification = "effective"
        elif score_with == "PASS" and score_without == "PASS":
            classification = "redundant"
        elif score_with == "FAIL" and score_without == "PASS":
            classification = "backfire"
        elif score_with == "FAIL" and score_without == "FAIL":
            classification = "ineffective"
        else:
            classification = "unclear"

        test_result = {
            "rule": tc["rule"],
            "category": tc.get("category", "unknown"),
            "scenario": tc["scenario"],
            "tension": tc.get("tension", ""),
            "classification": classification,
            "with_archetype": {"response": resp_with[:1000], "score": score_with},
            "without_archetype": {"response": resp_without[:1000], "score": score_without},
        }
        results["tests"].append(test_result)

        icon = {"effective": "++", "redundant": "==", "backfire": "!!", "ineffective": "--"}
        print(f" {icon.get(classification, '??')} with={score_with} without={score_without}")

        if verbose:
            print(f"\n    Scenario: {tc['scenario']}")
            print(f"    With archetype:\n      {resp_with[:300]}")
            print(f"    Without:\n      {resp_without[:300]}")
            print()

    # Summary
    total = len(results["tests"])
    if total == 0:
        results["summary"] = {}
        return results

    counts = {}
    for t in results["tests"]:
        c = t["classification"]
        counts[c] = counts.get(c, 0) + 1

    with_pass = sum(1 for t in results["tests"] if t["with_archetype"]["score"] == "PASS")
    without_pass = sum(1 for t in results["tests"] if t["without_archetype"]["score"] == "PASS")

    results["summary"] = {
        "total": total,
        "effective": counts.get("effective", 0),
        "redundant": counts.get("redundant", 0),
        "backfire": counts.get("backfire", 0),
        "ineffective": counts.get("ineffective", 0),
        "with_pass_rate": with_pass / total,
        "without_pass_rate": without_pass / total,
        "delta": (with_pass - without_pass) / total,
    }

    return results


def exclude_guards(guard_texts: list[str], reason: str, project_slug: str | None = None) -> int:
    """Mark guards as excluded in the profile. They stay in the data but are skipped during synthesis.

    reason: "backfire" or "redundant"
    Matches by SequenceMatcher similarity OR keyword substring overlap.
    """
    from difflib import SequenceMatcher

    profile = load_profile(project_slug=project_slug)
    dims = profile.get("dimensions", {})
    excluded = 0

    for dim in ["guards", "ai_failures"]:
        if dim not in dims:
            continue
        for pref in dims[dim]:
            if pref.get("excluded"):
                continue
            pref_lower = pref["observation"].lower()
            match = False
            for gt in guard_texts:
                gt_lower = gt.lower()
                # Exact match (benchmark now uses verbatim profile text)
                if pref_lower == gt_lower:
                    match = True
                    break
                # Prefix match (benchmark may truncate)
                if pref_lower.startswith(gt_lower[:80]) or gt_lower.startswith(pref_lower[:80]):
                    match = True
                    break
                # SequenceMatcher similarity as fallback
                if SequenceMatcher(None, pref_lower, gt_lower).ratio() > 0.5:
                    match = True
                    break
            if match:
                pref["excluded"] = reason
                excluded += 1

    if excluded:
        from synapptic.state import save_profile
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
        from synapptic.state import save_profile
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


def save_benchmark(results: dict):
    """Save benchmark results to disk."""
    BENCHMARKS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H%M")
    project = results.get("project", "global")
    path = BENCHMARKS_DIR / f"{project}_{timestamp}.json"
    with open(path, "w") as f:
        json.dump(results, f, indent=2)
    return path


def format_results(results: dict) -> str:
    """Format benchmark results for display."""
    if not results or not results.get("summary"):
        return "No results."

    s = results["summary"]
    lines = [
        f"Benchmark: {results['project']} ({s['total']} tests)",
        "",
        f"  With archetype:    {s['with_pass_rate']:.0%} pass",
        f"  Without archetype: {s['without_pass_rate']:.0%} pass",
        f"  Behavioral delta:  {s['delta']:+.0%}",
        "",
        f"  ++ Effective (archetype saved it):  {s['effective']}",
        f"  == Redundant (both pass):           {s['redundant']}",
        f"  -- Ineffective (both fail):         {s['ineffective']}",
        f"  !! Backfire (archetype made worse):  {s['backfire']}",
        "",
    ]

    icons = {"effective": "++", "redundant": "==", "backfire": "!!", "ineffective": "--", "unclear": "??"}
    for t in results["tests"]:
        icon = icons.get(t["classification"], "??")
        lines.append(f"  {icon} [{t['with_archetype']['score']:4s}/{t['without_archetype']['score']:4s}] [{t.get('category','?')[:12]:12s}] {t['rule'][:60]}")

    return "\n".join(lines)
