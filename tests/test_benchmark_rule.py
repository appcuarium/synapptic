"""The benchmark on one rule: the rule on against the rule off, through the harness. The scenario comes from
the generation model, each arm is answered by a fresh headless session with the user's hooks running (here a
stand-in that runs the real prompt hook for its session and answers from what it was shown), the store says
whether the rule reached the model, the judge scores both replies, and the verdict stays with the rule."""

import functools
import itertools
import json
import threading

import pytest
import synapptic_core

from synapptic import benchmark
from synapptic.harness import add_rule, switch_rule

RULE = "pytest-parallel"
TEXT = "pytest in this workspace always runs with -n 4."
SCENARIO = {"rule": TEXT, "scenario": "Quick, run pytest on this module and tell me the exact command.",
            "tension": "speed over the workspace's convention", "category": "scope_discipline"}
CONFIG = {"provider": "claude-cli", "model": "sonnet"}


@pytest.fixture
def own_store(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # boundary: filesystem - the verdict files go under this test's directory, not the user's
    monkeypatch.setattr(benchmark, "BENCHMARKS_DIR", tmp_path / "benchmarks")
    add_rule(RULE, ["pytest", "run the tests"], TEXT, "")
    return tmp_path


def respond_as_session(counter, prompt, model, cwd):
    """A headless session stand-in: the real prompt hook runs for a fresh session id, and the reply says what
    the model was shown (the command with -n 4 when the rule reached it)."""
    session = f"bench-{next(counter)}"
    done = synapptic_core.harness_pre(json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session, "prompt": prompt, "cwd": "/Users/x/proj"}))
    shown = "-n 4" in (done["stdout"] or "")
    return ("Run `pytest -n 4`." if shown else "Run `pytest`."), session


def headless_session(counter):
    return functools.partial(respond_as_session, counter)


def model_calls(prompt, config=None, temperature=None, session_id=None):
    """The generation and judge models: the scenario, and COMPLY only when the response (not the rule's own
    text) carries -n 4."""
    if "COMPLY" in prompt:
        hit = "-n 4" in prompt.split("Response:")[-1]
        return json.dumps({"verdict": "COMPLY" if hit else "VIOLATE", "reason": "parallel" if hit else "serial"})
    return json.dumps([SCENARIO])


def test_the_rule_on_wins_against_the_rule_off_through_the_hook(own_store, monkeypatch):
    # boundary: llm - the generation and judge models
    monkeypatch.setattr(benchmark, "call_llm", model_calls)
    steps = []

    verdict = benchmark.run_rule_benchmark(RULE, config=CONFIG, respond=headless_session(itertools.count()), progress=steps.append)

    assert verdict["classification"] == "effective" and verdict["fired"] == 3
    assert (verdict["with"]["passes"], verdict["with"]["valid"]) == (3, 3)
    assert (verdict["without"]["passes"], verdict["without"]["valid"]) == (0, 3)
    assert verdict["respondent"] == {"provider": "claude-cli", "model": "sonnet"} and verdict["scenario"] == SCENARIO["scenario"]
    # every off arm said so, every headless reply and every judge verdict was reported, and the rule is on again
    assert sum(e["kind"] == "note" and "live sessions" in e["message"] for e in steps) == 3
    assert [e["kind"] for e in steps if e["kind"] == "scenario"] == ["scenario"]
    replies = [e for e in steps if e["kind"] == "reply"]
    assert len(replies) == 6 and {e["arm"] for e in replies} == {"with", "without"} and all(e["session"].startswith("bench-") for e in replies)
    assert [e["score"] for e in steps if e["kind"] == "judge" and e["arm"] == "without"] == ["FAIL", "FAIL", "FAIL"]
    exported = json.loads(synapptic_core.harness_export())
    rule = next(r for r in exported["rules"] if r["rule"] == RULE)
    assert not rule.get("switched off") and rule["benchmark"][0]["classification"] == "effective"


def stop_after_first(cancel, answer, prompt, model, cwd):
    """The user presses Stop while the first answer is in flight."""
    cancel.set()
    return answer(prompt, model, cwd)


def test_a_stopped_run_keeps_what_ran_as_a_stopped_verdict(own_store, monkeypatch):
    """The Stop button sets the cancel; the run ends at its next arm and what ran so far is the verdict,
    marked stopped, kept with the rule like any other (operator 2026-11-05)."""
    monkeypatch.setattr(benchmark, "call_llm", model_calls)   # boundary: llm - the generation and judge models
    cancel = threading.Event()
    respond = functools.partial(stop_after_first, cancel, headless_session(itertools.count()))

    verdict = benchmark.run_rule_benchmark(RULE, config=CONFIG, respond=respond, scenario=SCENARIO, cancel=cancel)

    assert verdict["classification"] == "stopped"
    assert verdict["with"]["valid"] + verdict["without"]["valid"] == 1   # one arm answered, the next never started
    exported = json.loads(synapptic_core.harness_export())
    assert next(r for r in exported["rules"] if r["rule"] == RULE)["benchmark"][0]["classification"] == "stopped"


def test_a_scenario_that_never_brings_the_rule_up_is_untestable(own_store, monkeypatch):
    # boundary: llm - the generation and judge models over the network; the judge's verdicts answered here, the
    # scenario handed in below so no generation call happens
    monkeypatch.setattr(benchmark, "call_llm", model_calls)
    off_topic = {**SCENARIO, "scenario": "Tell me a joke about compilers."}   # no phrase of the rule

    verdict = benchmark.run_rule_benchmark(RULE, config=CONFIG, respond=headless_session(itertools.count()), scenario=off_topic)

    assert verdict["fired"] == 0 and verdict["classification"] == "untestable"


def test_a_rule_that_was_off_is_off_again_after_its_benchmark(own_store, monkeypatch):
    """The arms switch the rule as they need and put its own switch back (operator 2026-11-05, the Benchmark
    page runs every rule in scope): a rule the user had switched off is off again when the run ends."""
    monkeypatch.setattr(benchmark, "call_llm", model_calls)   # boundary: llm - the generation and judge models
    switch_rule(RULE, True)

    verdict = benchmark.run_rule_benchmark(RULE, config=CONFIG, respond=headless_session(itertools.count()), scenario=SCENARIO, was_off=True)

    assert verdict["fired"] == 3   # the on arm had it on
    exported = json.loads(synapptic_core.harness_export())
    assert next(r for r in exported["rules"] if r["rule"] == RULE)["switched off"] is True


def runner_calls(calls, prompt, config=None, temperature=None, session_id=None):
    """The judge as in model_calls; the runner (a model on Ollama, no hooks of its own) answers from what the
    prompt carries: the command with -n 4 only when the rule's card came ahead of the scenario."""
    if "COMPLY" in prompt:
        return model_calls(prompt, config, temperature, session_id)
    calls.append((prompt, config))
    return "Run `pytest -n 4`." if TEXT in prompt else "Run `pytest`."


def test_redundant_means_both_arms_perfect():
    """Operator 2026-11-06 ("this is not redundant, redundant only when the results are the same and both
    perfect results"): 3/3 on against 2/3 off is effective, both perfect is redundant, the same but short is
    ineffective, fewer on than off is backfire, no valid run untestable, one arm without a run unclear."""
    assert benchmark.classify_verdict(3, 3, 2, 3) == "effective"
    assert benchmark.classify_verdict(3, 3, 3, 3) == "redundant"
    assert benchmark.classify_verdict(2, 3, 2, 3) == "ineffective"
    assert benchmark.classify_verdict(0, 3, 0, 3) == "ineffective"
    assert benchmark.classify_verdict(1, 3, 2, 3) == "backfire"
    assert benchmark.classify_verdict(0, 0, 0, 0) == "untestable"
    assert benchmark.classify_verdict(2, 3, 0, 0) == "unclear"
    # the bar for effective (operator: "ineffective if its less than FULL/FULL or at most 4/5 of all runs"):
    # the rule on must pass every run or 4 of 5, beating the rule off is not enough on its own
    assert benchmark.classify_verdict(1, 3, 0, 3) == "ineffective"
    assert benchmark.classify_verdict(2, 3, 0, 3) == "ineffective"
    assert benchmark.classify_verdict(4, 5, 2, 5) == "effective"
    assert benchmark.classify_verdict(3, 5, 0, 5) == "ineffective"
    assert benchmark.classify_verdict(5, 5, 4, 5) == "effective"
    assert benchmark.classify_verdict(4, 5, 5, 5) == "backfire"


def test_stored_verdicts_are_read_with_the_current_rule(own_store):
    """Operator 2026-11-06 ("the last one is still marked as redundant despite having 3/3 vs 2/3"): a run kept
    with the old word is read with the current rule, its summary with it; a stopped verdict keeps its word."""
    arm = lambda passes, valid: {"passes": passes, "valid": valid, "score": "PASS", "reasons": [], "fired": 0, "replies": []}
    benchmark.save_scope_benchmark({"timestamp": "2026-11-05T20:00:00Z", "project": "", "choices": {"runs": 3}, "rules": 2, "controls": None, "stopped": False, "verdicts": [
        {"rule": "a", "classification": "redundant", "with": arm(3, 3), "without": arm(2, 3), "judge_failures": 0},
        {"rule": "b", "classification": "stopped", "with": arm(0, 0), "without": arm(0, 0), "judge_failures": 0},
    ]})
    run = benchmark.list_scope_benchmarks()[0]
    assert [v["classification"] for v in run["verdicts"]] == ["effective", "stopped"]
    assert run["summary"]["effective"] == 1 and run["summary"]["redundant"] == 0


def test_a_scope_run_carries_the_previous_clis_closing_summary():
    """Operator 2026-11-06 ("I had this output at the end of the benchmark before"): a run over a scope sums its
    verdicts the way the CLI's closing lines did: testable of total, the pass rates with the rule on and off
    with their intervals, the net impact with the rules improved and regressed, the judge's failures over its
    calls, the controls. The rates count hits over every judged run, not a per-rule majority (operator:
    "it should count the hits as impact, not the majority"): on 3+3+1 of 9, off 0+3+3 of 9."""
    arm = lambda passes, valid, score: {"passes": passes, "valid": valid, "score": score, "reasons": [], "fired": 0, "replies": []}
    run = {"project": "", "controls": {"comply": True, "violate": False, "reasons": {}}, "verdicts": [
        {"rule": "a", "classification": "effective", "with": arm(3, 3, "PASS"), "without": arm(0, 3, "FAIL"), "judge_failures": 1},
        {"rule": "b", "classification": "redundant", "with": arm(3, 3, "PASS"), "without": arm(3, 3, "PASS"), "judge_failures": 0},
        {"rule": "c", "classification": "untestable", "with": arm(0, 0, "UNKNOWN"), "without": arm(0, 0, "UNKNOWN"), "judge_failures": 0},
        {"rule": "d", "classification": "backfire", "with": arm(1, 3, "FAIL"), "without": arm(3, 3, "PASS"), "judge_failures": 0},
    ]}
    summary = benchmark.scope_summary(run)
    assert (summary["total"], summary["testable"], summary["effective"], summary["redundant"], summary["backfire"], summary["untestable"]) == (4, 3, 1, 1, 1, 1)
    assert summary["with_pass_rate"] == pytest.approx(7 / 9) and summary["without_pass_rate"] == pytest.approx(6 / 9)
    assert summary["delta"] == pytest.approx(1 / 9) and len(summary["with_ci_95"]) == 2 and 0 < summary["with_ci_95"][0] < 7 / 9 < summary["with_ci_95"][1] <= 1
    assert summary["judge_failures"] == 1 and summary["judge_calls"] == 18
    assert summary["control_comply"] is True and summary["control_violate"] is False


def test_a_chosen_runner_or_judge_brings_its_own_providers_settings():
    """Operator 2026-11-06 (one entry per provider, one default): a runner or judge on a provider other than
    the default takes that provider's own key, URL and model, never the default's; the default's own stay
    when it is the one chosen."""
    config = {"provider": "anthropic", "model": "claude-sonnet-5-5", "api_key": "sk-ant-key",
              "providers": {"openai": {"model": "gpt-5", "api_key": "sk-openai-key"},
                            "ollama": {"model": "qwen3", "api_url": "http://10.0.0.7:11434/v1"}}}
    openai = benchmark.runner_for("openai", config, 0.1).keywords["config"]
    assert (openai["provider"], openai["model"], openai["api_key"], openai.get("api_url")) == ("openai", "gpt-5", "sk-openai-key", None)
    ollama = benchmark.runner_for("ollama", config, 0.1).keywords["config"]
    assert (ollama["provider"], ollama["model"], ollama.get("api_key"), ollama["api_url"]) == ("ollama", "qwen3", None, "http://10.0.0.7:11434/v1")
    anthropic = benchmark.runner_for("anthropic", config, 0.1).keywords["config"]
    assert (anthropic["model"], anthropic["api_key"]) == ("claude-sonnet-5-5", "sk-ant-key")
    assert benchmark.respondent_model(config, "openai") == "gpt-5" and benchmark.respondent_model(config, "anthropic") == "claude-sonnet-5-5"


def test_a_runner_without_hooks_gets_the_rule_through_synapptics_own_hook(own_store, monkeypatch):
    """Any provider as the runner (operator 2026-11-06): a model on Ollama has no hooks of its own, so Synapptic
    runs its prompt hook around it with the rule switched as the arm needs: the on arm gets the rule's card
    ahead of the scenario because the scenario carries one of its words, the off arm the bare scenario, the
    store says the rule reached the on arm's session, and the rule is on again afterwards."""
    calls = []
    monkeypatch.setattr(benchmark, "call_llm", functools.partial(runner_calls, calls))   # boundary: llm - the runner and the judge

    verdict = benchmark.run_rule_benchmark(RULE, config=CONFIG, scenario=SCENARIO, provider="ollama", model="qwen3-coder-next:q4_K_M")

    assert verdict["classification"] == "effective" and verdict["fired"] == 3
    assert verdict["respondent"] == {"provider": "ollama", "model": "qwen3-coder-next:q4_K_M"}
    assert len(calls) == 6 and all(config["provider"] == "ollama" and config["model"] == "qwen3-coder-next:q4_K_M" for _, config in calls)
    prompts = sorted(prompt for prompt, _ in calls)
    assert prompts[:3] == [SCENARIO["scenario"]] * 3                      # the off arm: the scenario alone
    assert all(p.startswith(TEXT) and p.endswith(SCENARIO["scenario"]) for p in prompts[3:])   # the on arm: the card, then the scenario
    exported = json.loads(synapptic_core.harness_export())
    assert next(r for r in exported["rules"] if r["rule"] == RULE).get("switched off") is not True


def test_a_runner_without_hooks_is_untestable_when_the_scenario_never_brings_the_rule_up(own_store, monkeypatch):
    """Synapptic's hook is the real one: a scenario without any of the rule's words injects nothing on either
    arm, the store sees no fire, and the verdict is untestable, as with Claude Code."""
    calls = []
    monkeypatch.setattr(benchmark, "call_llm", functools.partial(runner_calls, calls))   # boundary: llm - the runner and the judge
    quiet = {**SCENARIO, "scenario": "Quick, execute the checks on this module and tell me the exact command."}

    verdict = benchmark.run_rule_benchmark(RULE, config=CONFIG, scenario=quiet, provider="ollama", model="qwen3-coder-next:q4_K_M")

    assert verdict["classification"] == "untestable" and verdict["fired"] == 0
    assert all(prompt == quiet["scenario"] for prompt, _ in calls)   # nothing ahead of the scenario on either arm


# ----- a run over the rules: the previous CLI's full benchmark (operator 2026-11-05) -------------------------

def generation_calls(calls, prompt, config=None, temperature=None, session_id=None):
    """The generation model: one scenario per asked rule, counted."""
    calls.append(prompt)
    return json.dumps([{**SCENARIO, "scenario": f"scenario {len(calls)}"}])


def test_the_scenarios_of_a_seed_are_generated_once_and_shared_by_every_rerun(own_store, monkeypatch):
    """The CLI's cached test cases: one scenario per rule, generated once per scope, seed, generation model and
    rule set, kept under the benchmarks directory and reused by every rerun with that seed (so the arms and the
    reruns face the same cases); refresh generates them again; another seed is another set."""
    calls = []
    monkeypatch.setattr(benchmark, "call_llm", functools.partial(generation_calls, calls))   # boundary: llm - the generation model
    add_rule("commit-only-on-order", ["commit"], "Commit only when told.", "")
    rules = [{"id": RULE}, {"id": "commit-only-on-order"}]
    notes = []

    first = benchmark.rule_scenarios(rules, "", 7, CONFIG, progress=notes.append)
    asked_first = len(calls)
    again = benchmark.rule_scenarios(rules, "", 7, CONFIG, progress=notes.append)
    asked_again = len(calls)
    fresh = benchmark.rule_scenarios(rules, "", 7, CONFIG, refresh=True)
    asked_fresh = len(calls)
    other = benchmark.rule_scenarios(rules, "", 8, CONFIG)

    assert list(first) == [RULE, "commit-only-on-order"] and first[RULE]["scenario"] == "scenario 1" and asked_first == 2
    assert again == first and asked_again == 2                       # the rerun read the kept scenarios, asked nothing
    assert "reusing 2 cached scenario(s) for seed 7" in [n["message"] for n in notes]
    assert fresh[RULE]["scenario"] == "scenario 3" and asked_fresh == 4
    assert other[RULE]["scenario"] == "scenario 5" and len(calls) == 6
    # the kept sets live in the database (operator 2026-11-06), two of them; forgotten, the next run asks again
    assert benchmark.flush_scenarios() == 2
    assert benchmark.rule_scenarios(rules, "", 7, CONFIG)[RULE]["scenario"] == "scenario 7" and len(calls) == 8
    assert not (own_store / "benchmarks" / "scenarios").exists()


def test_the_runs_and_scenarios_kept_as_files_are_imported_once(own_store, monkeypatch):
    """Before 2026-11-06 the runs and scenarios were files under the benchmarks directory: listing the runs
    imports them into the database in their order, renames the folders, and a second listing adds nothing."""
    import os
    runs_dir = own_store / "benchmarks" / "runs"
    runs_dir.mkdir(parents=True)
    for stamp, project in (("2026-11-05_131046", ""), ("2026-11-05_194041", "__all__")):
        with open(runs_dir / f"{stamp}.json", "w") as f:
            json.dump({"timestamp": stamp.replace("_", "T")[:10] + "T" + stamp[11:13] + ":" + stamp[13:15] + ":" + stamp[15:17] + "Z",
                       "project": project, "choices": {"runs": 1}, "rules": 1, "verdicts": [], "stopped": False}, f)
    scenarios_dir = own_store / "benchmarks" / "scenarios"
    scenarios_dir.mkdir()
    with open(scenarios_dir / "global_seed1_sonnet_abcdef123456.json", "w") as f:
        json.dump({"format_version": benchmark.SCENARIO_FORMAT, "seed": 1, "model": "sonnet", "scenarios": {RULE: SCENARIO}}, f)

    runs = benchmark.list_scope_benchmarks()
    again = benchmark.list_scope_benchmarks()

    assert [run["project"] for run in runs] == ["__all__", ""] and runs[0]["id"] == "2026-11-05_194041"   # newest first
    assert again == runs
    assert sorted(os.listdir(own_store / "benchmarks")) == ["runs.imported", "scenarios.imported"]
    # the imported scenarios serve a run with that seed over the same rule set, no generation asked
    monkeypatch.setattr(benchmark, "call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no generation expected")))   # boundary: llm - must stay silent
    assert benchmark.rule_scenarios([{"id": RULE}], "", 1, {"provider": "claude-cli", "model": "sonnet"})[RULE] == SCENARIO


def test_a_seed_picks_the_same_rules_in_the_same_order():
    rules = [{"id": name} for name in ("a", "b", "c", "d", "e")]
    picked = [rule["id"] for rule in benchmark.select_rules(rules, 3, 42)]
    assert picked == [rule["id"] for rule in benchmark.select_rules(rules, 3, 42)] and len(set(picked)) == 3
    assert picked != [rule["id"] for rule in benchmark.select_rules(rules, 3, 43)] or picked != ["a", "b", "c"]
    assert [rule["id"] for rule in benchmark.select_rules(rules, 9, 42)] == [rule["id"] for rule in benchmark.select_rules(rules, 5, 42)]


def control_judge(biased, prompt, config=None, temperature=None, session_id=None):
    """The judge on the control cases: right (COMPLY for the language rule, VIOLATE for the no-code rule), or
    biased toward COMPLY whatever the response."""
    if biased or "same language" in prompt:
        return json.dumps({"verdict": "COMPLY", "reason": "fine"})
    return json.dumps({"verdict": "VIOLATE", "reason": "there is code"})


def runner_answers(prompt, model, cwd):
    return "Sure, here is how: `sorted(items)`.", None


@pytest.mark.parametrize("biased, expected", [(False, {"comply": True, "violate": True}), (True, {"comply": True, "violate": False})])
def test_the_control_cases_read_the_judge_once_per_run(own_store, monkeypatch, biased, expected):
    """The CLI's judge calibration: the runner answers the two control scenarios, the judge reads each against
    the control's own rule; a judge biased toward COMPLY is caught by the violate control."""
    monkeypatch.setattr(benchmark, "call_llm", functools.partial(control_judge, biased))   # boundary: llm - the judge
    found = benchmark.judge_controls(CONFIG, runner_answers, "sonnet", None)
    assert {"comply": found["comply"], "violate": found["violate"]} == expected and set(found["reasons"]) == {"comply", "violate"}
