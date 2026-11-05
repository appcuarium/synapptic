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
from synapptic.harness import add_rule

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
    assert len(list((own_store / "benchmarks" / "rules").glob(f"{RULE}_*.json"))) == 1


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


def never_switch(rule_id, off):
    raise AssertionError("a runner without hooks never switches the rule in the store")


def runner_calls(calls, prompt, config=None, temperature=None, session_id=None):
    """The judge as in model_calls; the runner (a model on Ollama, no hooks) answers from what the prompt
    carries: the command with -n 4 only when the rule's text came ahead of the scenario."""
    if "COMPLY" in prompt:
        return model_calls(prompt, config, temperature, session_id)
    calls.append((prompt, config))
    return "Run `pytest -n 4`." if TEXT in prompt else "Run `pytest`."


def test_a_runner_without_hooks_gets_the_rule_ahead_of_the_scenario(own_store, monkeypatch):
    """Any provider as the runner (operator 2026-11-05): a model on Ollama has no hooks, so the on arm gets the
    rule's text ahead of the scenario from the benchmark itself, the off arm the bare scenario, the store is
    never switched (the user's live sessions keep the rule), and the on arm counts as reached every time."""
    calls = []
    monkeypatch.setattr(benchmark, "call_llm", functools.partial(runner_calls, calls))   # boundary: llm - the runner and the judge
    monkeypatch.setattr(synapptic_core, "harness_switch_rule", never_switch)

    verdict = benchmark.run_rule_benchmark(RULE, config=CONFIG, scenario=SCENARIO, provider="ollama", model="qwen3-coder-next:q4_K_M")

    assert verdict["classification"] == "effective" and verdict["fired"] == 3
    assert verdict["respondent"] == {"provider": "ollama", "model": "qwen3-coder-next:q4_K_M"}
    assert len(calls) == 6 and all(config["provider"] == "ollama" and config["model"] == "qwen3-coder-next:q4_K_M" for _, config in calls)
    prompts = sorted(prompt for prompt, _ in calls)
    assert prompts[:3] == [SCENARIO["scenario"]] * 3                      # the off arm: the scenario alone
    assert all(p.startswith(TEXT) and p.endswith(SCENARIO["scenario"]) for p in prompts[3:])   # the on arm: the rule, then the scenario
