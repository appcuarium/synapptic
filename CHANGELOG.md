# Changelog

## v0.1.0b3

### Behavioral benchmark + smart hook

This release adds `synapptic benchmark` - a personalized behavioral testing system that measures whether your archetype actually changes AI behavior. Also significantly improves the SessionEnd hook reliability.

**Benchmark (`synapptic benchmark`):**
- Generates adversarial test cases from YOUR archetype - not a generic test suite
- Each test creates tension where the "naturally helpful" response would violate a guard
- Regex-based hard checks for summary suppression, planning prevention, scope discipline
- LLM-as-judge for semantic rules that can't be pattern-matched
- Measures behavioral delta: `P(pass|with archetype) - P(pass|without archetype)`
- Classifies each guard: effective (archetype saved it), redundant (both pass), backfire (archetype made worse), ineffective (both fail)
- `--rerun` flag reuses cached test cases for tracking improvement over time
- `--seed` for reproducible test generation
- `--verbose` shows full prompts, scenarios, and responses
- `--seed` for reproducible tests, cached per seed for tracking over time
- `--refresh` regenerates cached tests when archetype changes
- Detects backfire guards (archetype makes behavior worse) and redundant guards (both pass)
- Prompts to exclude backfire/redundant guards after benchmark (marked, never deleted)
- `synapptic guards excluded` / `synapptic guards include` for viewing and re-including
- Synthesis skips excluded guards when generating archetype
- First real measurement: +20% to +50% behavioral delta on real profiles

**Hook improvements:**
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
