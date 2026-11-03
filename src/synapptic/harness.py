"""The harness feedback loop: refusals a coding-agent harness gave without having shown its context first
become proposals for the words that rule should have known; the user approves or rejects each one; an
approved proposal's words are written into the rule in the harness store.

The rules, the hooks and the loop's logic live in the `synapptic-core` package (compiled, installed
separately); this module is the plumbing around it: the proposal store, the model call, session lookup.
It never reads a rule's contents, only its id.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import yaml

from synapptic.config import CLAUDE_PROJECTS_DIR, SYNAPPTIC_DIR
from synapptic.core import CoreMissing, require_core as load_core
from synapptic.extract import extract_json
from synapptic.integrate import resolve_project_root
from synapptic.providers import call_llm, load_config
from synapptic.state import find_session_transcripts

HARNESS_DIR = SYNAPPTIC_DIR / "harness"
PROPOSALS_PATH = HARNESS_DIR / "proposals.yaml"
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

def proposal_id(rule_id: str, regex: str) -> str:
    return hashlib.sha1(f"{rule_id}\n{regex}".encode(), usedforsecurity=False).hexdigest()[:8]


def load_proposals() -> dict[str, dict]:
    try:
        data = yaml.safe_load(PROPOSALS_PATH.read_text()) or {}
    except OSError:
        return {}
    return data if isinstance(data, dict) else {}


def save_proposals(proposals: dict[str, dict]) -> None:
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp = PROPOSALS_PATH.with_suffix(f".{os.getpid()}.tmp")
    temp.write_text(yaml.safe_dump(proposals, sort_keys=False, allow_unicode=True))
    os.replace(temp, PROPOSALS_PATH)


def rejected_regexes(proposals: dict[str, dict], rule_id: str) -> set[str]:
    return {p["regex"] for p in proposals.values() if p.get("rule_id") == rule_id and p.get("status") == "rejected"}


def record_proposal(proposals: dict[str, dict], miss: dict, candidate: dict) -> dict:
    """Add the miss as a receipt of the proposal for (rule, regex), creating the proposal on its first receipt."""
    key = proposal_id(miss["rule_id"], candidate["regex"])
    proposal = proposals.setdefault(key, {
        "kind": "vocabulary", "status": "pending", "rule_id": miss["rule_id"],
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
