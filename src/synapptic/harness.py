"""The harness feedback loop: refusals a coding-agent harness gave without having shown its context first
become proposals for the words that rule should have known; the user approves or rejects each one; an
approved proposal is written into the rules file.

Off unless config.yaml names the rules file (`harness: {rules: <path>}`). The loop's logic lives in the
`synapptic-harness` package (a compiled extension, installed separately); this module is the plumbing
around it: configuration, the rules files, the proposal store, the model call, session lookup.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

import yaml

from synapptic.config import CLAUDE_PROJECTS_DIR, SYNAPPTIC_DIR
from synapptic.extract import extract_json
from synapptic.integrate import resolve_project_root
from synapptic.providers import call_llm, load_config
from synapptic.state import find_session_transcripts

try:
    import synapptic_harness as core
except ImportError:
    core = None

HARNESS_DIR = SYNAPPTIC_DIR / "harness"
PROPOSALS_PATH = HARNESS_DIR / "proposals.yaml"
OPERATOR_ENTRYPOINT = "cli"
PROMPT_EXCERPT = 240
NOT_INSTALLED = "the synapptic-harness package is not installed; the harness loop needs it"


class HarnessError(Exception):
    pass


def require_core():
    if core is None:
        raise HarnessError(NOT_INSTALLED)
    return core


# ----- rules -----------------------------------------------------------------------------------------

def rules_path(config: dict) -> Path | None:
    """The harness rules file config.yaml names, or None: the integration is off by default."""
    path = (config.get("harness") or {}).get("rules")
    return Path(os.path.expanduser(str(path))) if path else None


def read_rules(path: Path) -> list[dict]:
    try:
        return list(json.loads(path.read_text())["rules"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


def load_rules(global_path: Path, cwd: str = "") -> dict[str, dict]:
    """Rules by id: the global file, plus <project>/.claude/harness-rules.json for the project holding `cwd`.
    Each rule records the file it came from (`_file`)."""
    files = [global_path]
    root = require_core().project_root(cwd) if cwd else None
    if root:
        files.append(Path(root) / ".claude" / "harness-rules.json")
    rules = {}
    for file in files:
        for rule in read_rules(file):
            rule["_file"] = file
            rules[rule["id"]] = rule
    return rules


# ----- transcripts -----------------------------------------------------------------------------------

def mine_transcript(path: Path) -> list[dict]:
    return require_core().mine_transcript(str(path))


def transcript_prompts(path: Path) -> list[dict]:
    return require_core().transcript_prompts(str(path))


def phrase_regex(phrase: str) -> str:
    return require_core().phrase_regex(phrase)


# ----- proposer --------------------------------------------------------------------------------------

def llm_candidates(miss: dict, rule: dict, config: dict) -> list[dict]:
    family = rule.get("family") or {}
    prompt = require_core().candidate_prompt(miss["prompt"], miss["refusal"], (rule.get("pre") or {}).get("prompt", ""),
                                             family.get("anchor", ""), rule.get("message", ""),
                                             list(family.get("conventions") or []))
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


def propose(miss: dict, rule: dict, corpus: list[str], config: dict, rejected: set[str]) -> list[dict]:
    """The candidates for one miss that pass the gate, each with its noise count over `corpus`."""
    kept = []
    current = (rule.get("pre") or {}).get("prompt", "")
    for candidate in llm_candidates(miss, rule, config):
        if core.accepted(candidate["phrase"], candidate["regex"], miss["prompt"], current, sorted(rejected)):
            kept.append({**candidate, "matches": core.noise(candidate["regex"], corpus), "corpus": len(corpus)})
    return kept


# ----- store -----------------------------------------------------------------------------------------

def proposal_id(rule_id: str, regex: str) -> str:
    return hashlib.sha1(f"{rule_id}\n{regex}".encode()).hexdigest()[:8]


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


def record_proposal(proposals: dict[str, dict], miss: dict, candidate: dict, rule: dict) -> dict:
    """Add the miss as a receipt of the proposal for (rule, regex), creating the proposal on its first receipt."""
    key = proposal_id(miss["rule_id"], candidate["regex"])
    proposal = proposals.setdefault(key, {
        "kind": "vocabulary", "status": "pending", "rule_id": miss["rule_id"], "rules_file": str(rule["_file"]),
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
        pattern = require_core().write_vocabulary(proposal["rules_file"], proposal["rule_id"], proposal["regex"],
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


def corpus_prompts(rule: dict, transcripts: list[Path]) -> list[str]:
    """The prompts the user typed inside the rule's `where` scope. A transcript whose Claude project folder
    decodes to a root outside the scope is not opened; any other is read and its prompts checked by cwd."""
    where = re.compile(rule["where"]) if rule.get("where") else None
    corpus = []
    for path in transcripts:
        root = resolve_project_root(path.parent.name)
        if where and root and not where.search(str(root)):
            continue
        for prompt in transcript_prompts(path):
            if prompt["entrypoint"] in ("", OPERATOR_ENTRYPOINT) and (not where or where.search(prompt["cwd"])):
                corpus.append(prompt["text"])
    return corpus


def mine(transcripts: list[Path], config: dict | None = None, corpus_transcripts: list[Path] | None = None) -> list[dict]:
    """Mine the transcripts, propose for each miss, record the proposals. Returns one entry per miss:
    {miss, rule, proposals: [proposal dicts]} (an unknown rule or no surviving candidate gives an empty list)."""
    require_core()
    config = config or load_config()
    global_rules = rules_path(config)
    if not global_rules:
        raise HarnessError("the harness integration is off: set `harness: {rules: <path to harness-rules.json>}` in config.yaml")
    proposals = load_proposals()
    corpora: dict[str, list[str]] = {}
    results = []
    for path in transcripts:
        for miss in mine_transcript(path):
            rules = load_rules(global_rules, miss["cwd"])
            rule = rules.get(miss["rule_id"])
            entry = {"miss": miss, "rule": rule, "proposals": []}
            results.append(entry)
            if not rule:
                continue
            if miss["rule_id"] not in corpora:
                corpora[miss["rule_id"]] = corpus_prompts(rule, corpus_transcripts if corpus_transcripts is not None
                                                          else operator_transcripts())
            for candidate in propose(miss, rule, corpora[miss["rule_id"]], config, rejected_regexes(proposals, miss["rule_id"])):
                entry["proposals"].append(record_proposal(proposals, miss, candidate, rule))
    save_proposals(proposals)
    return results
