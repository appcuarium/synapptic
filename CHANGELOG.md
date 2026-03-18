# Changelog

## v0.1.0b3

### Command rename

- `synapptic update` → `synapptic ingest` - more expressive name for the full pipeline (extract → merge → synthesize → integrate)

### Benchmark redesign: Regex → LLM-as-Judge

Complete rewrite of the benchmark scoring system, driven by 3 rounds of adversarial review.

**Scoring: LLM-as-judge replaces regex:**
- `judge_response()` evaluates compliance via structured COMPLY/VIOLATE verdicts
- Failed judge calls return UNKNOWN (excluded from scoring, not silently counted as PASS)
- Judge reasoning stored per test for auditability
- `--judge-provider` / `--judge-model` flags for separate judge model (avoids self-evaluation bias)
- Warning when judge is the same model as respondent

**Correct experimental design:**
- Guard in archetype: WITH = full archetype, WITHOUT = archetype minus guard (ablation)
- Guard not in archetype: WITH = archetype + guard appended, WITHOUT = archetype as-is (additive test)
- Isolates each guard's individual contribution regardless of whether synthesis included it
- Guard removal handles multi-line entries (removes continuation lines at deeper indentation)
- Only "guards" dimension benchmarked (ai_failures are incident descriptions, not individually testable)

**Statistical rigor:**
- Default `--runs 3` with majority vote (was 1)
- Ties on even valid_runs → "unclear" (not silent FAIL)
- Wilson score 95% confidence intervals on pass rates (suppressed at n<5)
- CI caveat: "assumes independent tests (guards may be correlated)"
- Two-directional control tests: COMPLY control (expect redundant) + VIOLATE control (expect ineffective) — detects judge bias in either direction

**Temperature support:**
- `--temperature` flag (default 0.1) for response generation
- Passed through to all providers: anthropic, ollama, openai, gemini, lmstudio, custom
- claude-cli: warns that temperature is unsupported, lists providers that support it

**Guard quality:**
- Test-to-guard fidelity validation (SequenceMatcher, drops hallucinated guards)
- Near-duplicate guard deduplication (Jaccard token similarity, O(n·k))
- Guards with weight < 0.3 filtered with visible count
- Balanced order randomization: forces at least 1 with-first + 1 without-first per test

**Cache & storage:**
- Cache format v4 with guard-list hash — profile changes auto-invalidate stale caches
- Single `benchmarks/` directory (removed duplicate `benchmark_results/` dir)
- Result filenames include all params: `{project}_{provider}_{model}_seed{seed}_t{temp}_{timestamp}.json`
- Judge and storage truncation aligned at 4000 chars
- Response failures tracked separately from judge failures

**Output improvements:**
- "Guard compliance" / "Baseline compliance" / "Guard impact" (was "Archetype compliance")
- Net impact with gross breakdown: `+10% net (3 improved, 1 regressed)`
- Judge health line: failure count, failure rate, control status
- Per-test: vote counts, judge errors, response errors

**`results compare` fixed:**
- Was completely broken (referenced non-existent keys)
- Now shows guard compliance, impact delta, effective/backfire counts, winner

### Integration fix

**Claude Code MEMORY.md archetype placement:**
- Archetype reference (`user_archetype.md`) now inserted near the top of MEMORY.md instead of appended at the bottom
- Claude Code truncates MEMORY.md after line 200 — previous behavior appended at the end, causing the archetype to be invisible in projects with large MEMORY.md files
- Existing projects where the reference is past line 200 are automatically fixed on next `synapptic ingest`

### CI and testing

- GitHub Actions workflow: runs `pytest` on Python 3.10, 3.11, 3.12 on push/PR to master, develop, release branches
- Tests badge added to README
- 82 tests: guard selection (15), judge response parsing (15), majority vote (6), test fidelity (5), guard dedup (5), guard removal (12), confidence intervals (5), + filter/profile tests

### Hook improvements

- Only processes explicitly closed sessions (filters by `reason=prompt_input_exit|clear|logout`)
- Derives project from `transcript_path` in the hook JSON input (no `find` needed)
- Synthesizes only global + the affected project (not all 15 projects)
- PID file guard replaces `pgrep` (which falsely matched bash wrapper command strings)
- `sleep 2` before checking transcript ensures `last-prompt` record is written

**New provider:**
- Google Gemini (`gemini-3.1-flash-lite-preview`) added as LLM provider

**Provider system:**
- No default provider - if unconfigured, tells user to run `synapptic init`
- No silent fallback between providers
- Model defaults reset when switching providers

**Extraction:**
- Transcript wrapped in `<transcript>` tags with injection guard (prevents LLM from following instructions inside the transcript)

## v0.1.0b2

### Calibration release

Focused on extraction quality and profile accuracy after real-world testing across 30+ sessions.

**Extraction improvements:**
- Sessions now processed biggest-first (sort by file size descending)
- Skipped sessions no longer count against `--limit` - you get the number of actual extractions you asked for
- Profile-aware extraction tells the LLM to skip known patterns and focus on new signal

**Profile calibration:**
- Similarity threshold tuned to 0.7 (balances dedup vs false merges)
- Evidence threshold lowered to 1 (weight is the primary quality filter)
- Synthesis prompt now requires guards to include scope limits ("read 1-2 existing usages" not "read all")

**Provider fixes:**
- Model defaults reset when switching providers (no more `local-model` leaking into Claude CLI)
- Better error logging: shows exit code and stderr/stdout on LLM failures
- Descriptive messages when synthesis is skipped (explains why and what to do)

**Hook safety:**
- Uses `pgrep` to detect any running synapptic process before starting (prevents conflicts between hook and manual runs)
- Removed hardcoded `--model sonnet` from hook (uses config)

**Benchmark results (same 3 test prompts, before/after calibration):**
- Summary suppression: failed -> passed
- Read-before-claiming: 26 tool calls, 79K tokens -> 2 searches + 1 read
- Pattern matching: 36 tool calls, 91K tokens, no clarification -> 5 reads + clarifying question

## v0.1.0b1

### Initial release

- Three-tier pipeline: filter, extract, merge, synthesize, integrate
- Per-project + global profiles with dimension routing
- 9 observation dimensions (user + AI failure profiling)
- Multi-provider LLM backend (Claude CLI, Anthropic, OpenAI, Ollama, LM Studio, custom)
- Multi-platform output (Claude Code, Cursor, Copilot, Gemini)
- Custom extraction patterns
- SessionEnd hook for automatic background processing
- Interactive config: provider, profiling mode, output targets
- Clean install/uninstall with artifact preservation prompt
