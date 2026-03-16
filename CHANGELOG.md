# Changelog

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
