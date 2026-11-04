"""The harness feedback loop: refusals a coding-agent harness gave without having shown its context first
become proposals for the words that rule should have known; the user approves or rejects each one; an
approved proposal's words are written into the rule in the harness store.

The rules, the hooks and the loop's logic live in the `synapptic-core` package (compiled, installed
separately); this module is the plumbing around it: the proposal store, the model call, session lookup.
It never reads a rule's contents, only its id.
"""

import json
import os
import time
from pathlib import Path

from synapptic.config import CLAUDE_PROJECTS_DIR
from synapptic.core import CoreMissing, require_core as load_core
from synapptic.extract import extract_json
from synapptic.integrate import resolve_project_root
from synapptic.providers import call_llm, load_config, save_config
from synapptic.state import find_session_transcripts

PROMPT_EXCERPT = 240


class HarnessError(Exception):
    pass


def require_core():
    """The core, or a HarnessError the CLI shows (one message for every module: synapptic.core's)."""
    try:
        return load_core()
    except CoreMissing as exc:
        raise HarnessError(str(exc)) from exc


# ----- rules -----------------------------------------------------------------------------------------

def rule_ids() -> list[str]:
    """The ids of the rules in the harness store."""
    return list(require_core().harness_rule_ids())


# ----- transcripts -----------------------------------------------------------------------------------

def mine_transcript(path: Path) -> list[dict]:
    return require_core().mine_transcript(str(path))


def transcript_prompts(path: Path) -> list[dict]:
    return require_core().transcript_prompts(str(path))


def phrase_regex(phrase: str) -> str:
    return require_core().phrase_regex(phrase)


# ----- proposer --------------------------------------------------------------------------------------

def llm_candidates(miss: dict, config: dict) -> list[dict]:
    prompt = require_core().harness_candidate_prompt(miss["rule_id"], miss["prompt"], miss["refusal"])
    raw = call_llm(prompt, config=config)
    if not raw:
        return []
    try:
        parsed = json.loads(extract_json(raw))
    except ValueError:
        return []
    if not isinstance(parsed, list):
        return []
    phrases = [" ".join(str(item.get("phrase", "")).split()) for item in parsed if isinstance(item, dict)]
    return [{"phrase": phrase, "regex": phrase_regex(phrase)} for phrase in phrases if phrase]


def propose(miss: dict, corpus: list[str], config: dict, rejected: set[str]) -> list[dict]:
    """The candidates for one miss that pass the gate, each with its noise count over `corpus`."""
    kept = []
    core = require_core()
    for candidate in llm_candidates(miss, config):
        if core.harness_accepted(miss["rule_id"], candidate["phrase"], candidate["regex"], miss["prompt"], sorted(rejected)):
            kept.append({**candidate, "matches": core.noise(candidate["regex"], corpus), "corpus": len(corpus)})
    return kept


# ----- store -----------------------------------------------------------------------------------------
# Proposals live in the harness store (the core), where the hooks read the pending ones in shadow.

def proposal_id(rule_id: str, regex: str) -> str:
    return require_core().harness_proposal_key(rule_id, regex)


def load_proposals() -> dict[str, dict]:
    """Every proposal, keyed, in stored order."""
    return {proposal["key"]: proposal for proposal in require_core().harness_proposals()}


def save_proposals(proposals: dict[str, dict]) -> None:
    core = require_core()
    for key, proposal in proposals.items():
        core.harness_upsert_proposal(json.dumps({**proposal, "key": key}))


def rejected_regexes(proposals: dict[str, dict], rule_id: str) -> set[str]:
    """The words the user rejected, the ones an undo or the tuner reverted, and the ones the tuner narrowed to a
    two-word form: none is proposed again."""
    return {p["regex"] for p in proposals.values() if p.get("rule_id") == rule_id and p.get("status") in ("rejected", "reverted", "narrowed")}


def record_proposal(proposals: dict[str, dict], miss: dict, candidate: dict) -> dict:
    """Add the miss as a receipt of the proposal for (rule, regex), creating the proposal on its first receipt."""
    key = proposal_id(miss["rule_id"], candidate["regex"])
    proposal = proposals.setdefault(key, {
        "key": key, "kind": "vocabulary", "status": "pending", "rule_id": miss["rule_id"],
        "phrase": candidate["phrase"], "regex": candidate["regex"], "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "receipts": []})
    proposal["matches"] = candidate["matches"]
    proposal["corpus"] = candidate["corpus"]
    if not any(r["tool_use_id"] == miss["tool_use_id"] for r in proposal["receipts"]):
        proposal["receipts"].append({"session": miss["session"], "tool_use_id": miss["tool_use_id"],
                                     "timestamp": miss["timestamp"], "prompt": miss["prompt"],
                                     "prompt_excerpt": miss["prompt"][:PROMPT_EXCERPT], "refusal": miss["refusal"]})
    return proposal


# ----- decisions -------------------------------------------------------------------------------------

def approve(key: str) -> dict:
    proposals = load_proposals()
    proposal = proposals.get(key)
    if not proposal:
        raise HarnessError(f"no proposal {key}")
    if proposal["status"] != "pending":
        raise HarnessError(f"proposal {key} is already {proposal['status']}")
    try:
        pattern = require_core().harness_extend_prompt(proposal["rule_id"], proposal["regex"],
                                                       [r["prompt"] for r in proposal["receipts"]])
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc
    proposal["status"] = "approved"
    proposal["decided"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    proposal["written"] = pattern
    save_proposals(proposals)
    return proposal


def reject(key: str) -> dict:
    proposals = load_proposals()
    proposal = proposals.get(key)
    if not proposal:
        raise HarnessError(f"no proposal {key}")
    proposal["status"] = "rejected"
    proposal["decided"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    save_proposals(proposals)
    return proposal


# ----- mining ----------------------------------------------------------------------------------------

def resolve_session(name: str) -> Path:
    """A transcript path, or a session id (prefix) looked up under every Claude Code project folder."""
    path = Path(os.path.expanduser(name))
    if path.is_file():
        return path
    matches = sorted(CLAUDE_PROJECTS_DIR.glob(f"*/{name}*.jsonl"))
    if not matches:
        raise HarnessError(f"no transcript for session {name}")
    return matches[0]


def operator_transcripts() -> list[Path]:
    """The user's own sessions, as the sessions index selects them."""
    return [path for path, _ in find_session_transcripts(include_headless=False).values()]


def corpus_prompts(rule_id: str, transcripts: list[Path]) -> list[str]:
    """The prompts the user typed inside the rule's scope: each transcript with its Claude project folder decoded
    to a root (or "" when it cannot be), the core applies the rule's scope to the root and to each prompt's cwd."""
    pairs = [(str(path), str(resolve_project_root(path.parent.name) or "")) for path in transcripts]
    return list(require_core().harness_corpus_prompts(rule_id, pairs))


def stats(days: int = 14, transcripts: list[Path] | None = None) -> dict:
    """The tuner's numbers over the last `days`: per rule fires, idle fires, misses, characters, sessions,
    and per word of its vocabulary, from the store's events joined with the user's own sessions."""
    paths = transcripts if transcripts is not None else operator_transcripts()
    return require_core().harness_stats(days, [str(path) for path in paths])


# ----- the tuner -------------------------------------------------------------------------------------
# Approving is the harness's job: one pass promotes the pending proposals whose evidence is there (the
# core decides), every change is a version, the user keeps the undo and one dial: how readily the words
# change (`harness.tuning` in the config: off, conservative, normal or eager).

TUNING_LEVELS = ("off", "conservative", "normal", "eager")
TUNING_DEFAULT = "normal"
RULE_WINDOW_DAYS = 14   # the window the numbers and the tuning pass look back over


def tuning_level(config: dict | None = None) -> str:
    """The dial as configured; an unknown or missing value is the default."""
    config = config if config is not None else load_config()
    level = (config.get("harness") or {}).get("tuning", TUNING_DEFAULT)
    return level if level in TUNING_LEVELS else TUNING_DEFAULT


def set_tuning_level(level: str) -> dict:
    """Write the dial into the config file and return the config."""
    if level not in TUNING_LEVELS:
        raise HarnessError(f"unknown tuning level {level}: {', '.join(TUNING_LEVELS)}")
    config = load_config()
    config["harness"] = {**(config.get("harness") or {}), "tuning": level}
    save_config(config)
    return config


def tune(days: int = 14, transcripts: list[Path] | None = None, level: str | None = None) -> dict:
    """One tuning pass: {"days", "level", "promoted": [...], "held": [...], "idle": [...]}, each entry with the key,
    rule, pattern and the numbers the decision rests on; a held entry carries its reason, an idle entry what became
    of the word (narrowed, reverted, demoted, kept). The transcripts go with their project roots, as the corpus
    does. The level is the dial's unless given; off leaves the words as they are and says so."""
    level = level or tuning_level()
    if level == "off":
        return {"days": days, "level": level, "promoted": [], "held": [], "idle": []}
    paths = transcripts if transcripts is not None else operator_transcripts()
    pairs = [(str(path), str(resolve_project_root(path.parent.name) or "")) for path in paths]
    try:
        return require_core().harness_tune(days, pairs, level)
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc


def changes(limit: int = 20) -> list[dict]:
    """The last changes to the rules, newest first: id, ts, rule_id, field, before, after, kind, key, numbers, undone."""
    try:
        return list(require_core().harness_changes(limit))
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc


def undo(version_id: str) -> dict:
    """Put a rule's words back to what they were before the change; the proposal behind it is never promoted again."""
    try:
        return require_core().harness_undo(version_id)
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc


# ----- the user's own hand on the rules (the `synapptic guards` commands) ------------------------------

def _call(function, *args):
    try:
        return function(*args)
    except ValueError as exc:
        raise HarnessError(str(exc)) from exc


def word_regex(word: str) -> str:
    """A word as typed by the user: a plain phrase becomes its regex, anything with regex syntax is taken as is."""
    plain = all(ch.isalnum() or ch in " '-" for ch in word)
    return phrase_regex(word) if plain else word


def add_rule(rule_id: str, phrases: list[str], text: str, where: str = "") -> str:
    """A hand-written rule: its card shows when a prompt matches one of the phrases, inside `where` when given."""
    words = "|".join(phrase_regex(phrase) for phrase in phrases if phrase.strip())
    return _call(require_core().harness_add_rule, json.dumps({"id": rule_id, "words": words, "text": text, "where": where}))


def remove_rule(rule_id: str) -> bool:
    return _call(require_core().harness_remove_rule, rule_id)


def switch_rule(rule_id: str, off: bool) -> bool:
    return _call(require_core().harness_switch_rule, rule_id, off)


def exclude_word(rule_id: str, word: str) -> dict:
    """Take a word out of a rule and keep it out: the tuner never proposes it again for that rule."""
    return _call(require_core().harness_exclude_word, rule_id, word_regex(word))


def include_word(rule_id: str, word: str) -> dict:
    """Put a word back into a rule (approved, live from the next prompt)."""
    return _call(require_core().harness_include_word, rule_id, word_regex(word))


def excluded_words() -> list[dict]:
    """The words kept out, per rule: {key, rule_id, phrase, regex}."""
    return [{"key": key, "rule_id": p["rule_id"], "phrase": p.get("phrase", ""), "regex": p["regex"]}
            for key, p in load_proposals().items() if p.get("status") == "rejected"]


def mine(transcripts: list[Path], config: dict | None = None, corpus_transcripts: list[Path] | None = None) -> list[dict]:
    """Mine the transcripts, propose for each miss, record the proposals. Returns one entry per miss:
    {miss, rule (whether the store knows it), proposals: [proposal dicts]}."""
    known = set(rule_ids())
    if not known:
        raise HarnessError("the harness store holds no rules; run `synapptic install` and migrate your rules first")
    config = config or load_config()
    proposals = load_proposals()
    corpora: dict[str, list[str]] = {}
    results = []
    for path in transcripts:
        for miss in mine_transcript(path):
            entry = {"miss": miss, "rule": miss["rule_id"] in known, "proposals": []}
            results.append(entry)
            if not entry["rule"]:
                continue
            if miss["rule_id"] not in corpora:
                corpora[miss["rule_id"]] = corpus_prompts(miss["rule_id"], corpus_transcripts if corpus_transcripts is not None
                                                          else operator_transcripts())
            for candidate in propose(miss, corpora[miss["rule_id"]], config, rejected_regexes(proposals, miss["rule_id"])):
                entry["proposals"].append(record_proposal(proposals, miss, candidate))
    save_proposals(proposals)
    return results
