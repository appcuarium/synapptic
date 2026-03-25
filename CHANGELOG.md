# Changelog

## v0.1.0b4 — 2026-03-25

### Unified dashboard

A single page at `/dashboard/` that combines session browsing, live conversation streaming, and real-time token metrics.

- **Three-panel layout** — projects on the left, conversation in the center, session list on the right
- **Full conversation rendering** — markdown, collapsible tool calls as inline pills, token usage per turn
- **Live streaming** — active sessions update in real-time via WebSocket, green dot indicator
- **Real-time metrics** — sticky bar shows requests, input/output tokens, cache usage, and estimated cost — pushed via WebSocket, no polling
- **Compaction visibility** — purple banners mark where context was compacted with pre-compaction token count
- **Full-text search** — FTS5 search across session titles and first messages
- **SQLite session index** — `synapptic index` scans all sessions once (~15s for 4000 files), incremental after that
- **Sensitive data scrubbing** — API keys, tokens, PEM keys, URL credentials, and high-entropy strings redacted before display
- **Machine FE color palette** — matching dark slate theme

### Session browser: live session lifecycle

- **Per-session token stats** — clicking any session shows its token usage (input, output, cache read/write) and estimated cost, read directly from JSONL `usage` fields rather than relay totals
- **Session-end notification** — the SessionEnd hook fires `POST /browser/api/session-ended`; the green dot disappears and the session leaves the active list instantly, no polling delay
- **Instant active session appearance** — on the first API request through the relay the server pushes an `active_check` WebSocket event; the active session appears in the sidebar within 400ms of the first message, without waiting for the 5-second poll
- **Live metrics only for selected session** — metrics accumulate continuously across all relay requests but are pushed to the browser only when that session is actively selected; switching sessions stops the push immediately
- **Metrics bar always visible on click** — the stats bar appears whenever a session is clicked (historical or active); historical sessions show JSONL totals, active sessions show live-updating relay metrics

### Relay

Local server that sits between your AI tool and the LLM API. Everything stays on your machine.

- **`synapptic run <tool>`** — launches any AI tool through the relay (Claude, Cursor, Copilot, Aider, Codex, Windsurf)
- **Token summary on exit** — requests, input/output tokens, cache usage
- **`synapptic relay enable/start/stop/status`** — full lifecycle management
- **Optional install** — `pip install synapptic[relay]` (fastapi, httpx, uvicorn)

### Benchmark improvements

- **Per-provider, per-model rate limiting** — sliding window TPM/RPM with proactive throttling
- **Dynamic batch sizing** — halves chunk size on 429, re-queues failed work
- **Per-model guard verdicts** — benchmark results stored per model in the profile
- **`--target-model`** on `synapptic synthesize` — filter guards by model benchmark results
- **`--chunks`** option — manual chunk override for large guard sets
- **Groq** added as first-class provider with 5 models and per-model rate limits
- **503 retry** — 10s/20s/30s backoff on service unavailable
- **Daily limit detection** — stops immediately on TPD/RPD errors

### Output targets

- Added Codex CLI, Windsurf, Cline, Aider, Continue.dev writers
- Updated Gemini path to `.gemini/styleguide.md`
- Per-model guard filtering — guards excluded per output target based on benchmark verdicts

### Security

- Three-layer sensitive data scrubbing (provider prefixes, keyword-value pairs, Shannon entropy); Gemini `AIza` key prefix added
- Config file 0600 permissions
- **HTTP hostname parsing** — loopback check uses `urlparse().hostname`; subdomain lookalikes like `localhost.attacker.com` are correctly blocked
- **file:// scheme blocked** — `call_openai_compatible` and `call_ollama` reject any URL that does not start with `http://` or `https://`
- **Non-loopback HTTP blocked** — plain HTTP URLs return `None`; only HTTPS or loopback connections are forwarded
- **Path traversal** — `path.relative_to(home)` replaces `str(path).startswith(str(home))`, which accepted `/home/alice_evil` as a child of `/home/alice`
- **Benchmark envelope nonce** — per-run random 16-char hex token embedded in envelope markers (`===BEGIN_PROFILE_{nonce}===`), preventing archetype content from forging the boundary and injecting instructions into the benchmark prompt
- **Transcript injection guard** — filtered transcript wrapped in `<transcript>` tags with "REFERENCE ONLY" instruction before passing to extraction LLM
- **Profile injection guard** — profile YAML wrapped in `<profile_yaml>` tags with "REFERENCE ONLY" instruction before synthesis
- stderr/HTTP body redaction across all providers

### Bug fixes

- **Atomic writes** — all 9 output writers and `save_profile` use fsync + rename, preventing half-written files on crash or power loss
- **Stable profile key** — pre-decay weight lookup uses full observation text instead of a truncated 120-char prefix, fixing collisions on observations with shared prefixes
- **Budget floor removed** — the `max(50, ...)` floor in proportional truncation allowed one very long turn to absorb the entire token budget
- **Rate limit double-multiplier removed** — `rate_limit_record` was called with `est_tokens * 1.5`, applying a buffer on top of an already-conservative estimate
- Cache key collision fix (md5), dedup threshold 0.8, time-based decay
- Double-decay rounding fix, sentence boundary awareness
- Invalid dimension warning, unified BENCHMARKS_DIR, global state reset
- re.sub lambda replacement, lstrip corruption fix

### Test coverage

- **~340 tests** across 14 files (was 163 in b3)
- New: test_cli, test_benchmark_results, test_config, test_patterns, test_state, test_outputs, test_extract, test_integrate, test_synthesize, test_providers, test_scrub

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
