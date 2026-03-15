# synapptic

![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![PyPI](https://img.shields.io/pypi/v/synapptic)
![Beta](https://img.shields.io/badge/status-beta-orange)

*The missing synapse between you and your AI agents.*

**synapptic** analyzes your AI coding sessions and builds a living profile that your agent loads at the start of every conversation. Not just your preferences - it detects interaction patterns you didn't even notice: the corrections you keep making, the assumptions your AI gets wrong, the workflow quirks that matter to you but you never thought to write down.

The difference between memory files you write yourself (CLAUDE.md, .cursorrules) and what synapptic generates is that you can only document what you're aware of. synapptic sees the patterns underneath - the things that cause friction without you realizing why. It watches fifty sessions and tells your AI: "this person interrupts when you over-investigate, stops reading after the second paragraph, and will lose trust if you claim something without checking the code first."

The result is simple: you stop fighting the model. You stop repeating yourself. You get back into flow - the state where you're thinking about your code, not about how to make yourself understood.

## Get started

```bash
pip install synapptic
synapptic init       # pick your LLM provider and output targets
synapptic install    # set up automatic session processing
synapptic update     # analyze your existing sessions
```

That's it. From now on, every session ends with synapptic quietly learning in the background. The next session starts smarter.

## What it builds

After analyzing your sessions, synapptic produces a living document with three sections:

```markdown
## User Archetype

You are working with a senior full-stack engineer who expects execution,
not explanation. Terse commands, no pleasantries. Read diffs, don't
summarize them.

## Guards

1. NEVER commit without running tests first
2. BEFORE implementing a new service, read an existing one of the same type
3. WHEN the user specifies a verification path, treat it as a hard constraint
4. NEVER write a post-implementation summary

## Known Weaknesses

- Confident claims without evidence
- Scope creep on focused fixes
- Planning theater ("let me plan this" for implementation tasks)
```

This loads automatically at session start. Your AI already knows the rules before you type a word.

## Works with everything

### Any LLM provider for processing

| Provider | Setup | Cost |
|----------|-------|------|
| **Claude CLI** | Already authenticated via Claude Code | Uses your plan |
| **Anthropic API** | API key | ~$0.30-0.80/session |
| **OpenAI API** | API key | ~$0.20-0.60/session |
| **Ollama** | Running locally | Free |
| **LM Studio** | Running locally | Free |
| **Custom** | Any OpenAI-compatible endpoint | Varies |

### Any AI coding assistant for output

| Assistant | Where synapptic writes |
|-----------|----------------------|
| **Claude Code** | `~/.claude/projects/*/memory/user_archetype.md` |
| **Cursor** | `.cursor/rules/synapptic.mdc` |
| **GitHub Copilot** | `.github/copilot-instructions.md` |
| **Gemini** | `GEMINI.md` |

Use one or all of them. synapptic writes to every target you configure - one command, all your tools stay in sync.

### Session sources

synapptic currently reads session transcripts from **Claude Code** (`~/.claude/projects/*/*.jsonl`), which stores full conversation history as structured JSONL. The profile it builds from those sessions is universal - the guards, preferences, and patterns apply to any AI assistant, not just Claude.

Support for additional session sources (Cursor chat history, Copilot logs, manual transcript import) is planned.

## How it works

```
Your session transcripts
    ↓ filter (626x compression - keeps only what matters)
Conversation pairs
    ↓ extract (LLM analyzes your interactions)
Raw observations across 9 dimensions
    ↓ merge (weighted accumulation - patterns strengthen, noise fades)
Living profile
    ↓ synthesize (LLM writes the narrative)
Archetype document
    ↓ integrate (writes to your tools)
Claude Code, Cursor, Copilot, Gemini - all updated
```

### What it looks for

synapptic extracts across nine dimensions, split between who you are (global) and what goes wrong in each project:

| | Global (follows you everywhere) | Per-project (specific to each codebase) |
|---|--------|-------------|
| **About you** | Communication style, workflow patterns, values, expertise | Code style, expectations |
| **About the AI** | Common anti-patterns (promoted from 2+ projects) | Failure patterns, behavioral guards |

Patterns that keep appearing across multiple projects automatically promote to global. A guard that started in one project becomes universal once the AI makes the same mistake in another.

### It gets smarter over time

- **Weighted decay**: Patterns that keep appearing get stronger. Old patterns that stop appearing naturally fade. Your profile evolves as you do.
- **Profile-aware extraction**: After the first run, synapptic sends your existing profile to the LLM so it skips known patterns and focuses on what's genuinely new. Less redundancy, lower cost.
- **Guards from day one**: When the AI makes a concrete mistake, the corresponding guard enters your profile immediately - no need to wait for it to happen twice.

## Custom extraction patterns

synapptic ships with a default extraction pattern, but you can create your own - different prompts for different use cases:

```bash
synapptic patterns list              # see available patterns
synapptic patterns create security   # create from template
synapptic patterns use security      # activate it
```

Each pattern is a `prompt.md` file in `~/.synapptic/patterns/`. Edit it to focus on whatever matters to you - security practices, performance patterns, team conventions - and synapptic will extract those dimensions from your sessions.

## Automatic background processing

After `synapptic install`, a session-end hook runs `synapptic update` in the background every time you close a session. Fully detached - you won't notice it. If it fails (network issue, rate limit), the next session catches up automatically. Nothing is ever lost.

## Configuration

```bash
synapptic init                # guided setup for everything below
synapptic config show         # see current settings
synapptic config provider     # change LLM provider
synapptic config mode         # profile user, AI, or both
synapptic config outputs      # choose output targets
```

### Profiling modes

Choose what synapptic should focus on:

- **both** (default): Extracts your preferences AND identifies AI failures
- **user**: Only your preferences, workflow, communication style
- **agent**: Only AI failure patterns and behavioral guards

## All commands

```bash
# Setup
synapptic init                      # guided first-time setup
synapptic install                   # deploy skill + session hook
synapptic config show               # view settings

# Processing
synapptic update                    # full pipeline (extract → merge → synthesize → write)
synapptic extract --all             # extract all unprocessed sessions
synapptic extract -s <UUID>         # extract one session
synapptic merge                     # merge observations into profiles
synapptic synthesize                # regenerate archetypes

# Viewing
synapptic stats                     # sessions processed, per-project breakdown
synapptic profile                   # weighted preferences
synapptic profile -p <project>      # one project's profile
synapptic archetype                 # the document your AI reads

# Patterns
synapptic patterns list             # available extraction patterns
synapptic patterns show <name>      # view a pattern
synapptic patterns create <name>    # create custom pattern
synapptic patterns use <name>       # activate a pattern

# Maintenance
synapptic diff                      # changes since last version
synapptic rollback                  # restore previous profile
synapptic reset                     # start fresh
synapptic uninstall                 # clean removal (asks before deleting data)
```

## Project structure

```
~/.synapptic/
├── config.yaml              # provider, model, mode, output targets
├── patterns/                # custom extraction patterns
├── global/                  # your profile (same across all projects)
│   ├── observations/
│   ├── profile.yaml
│   └── archetype.md
├── projects/
│   ├── <project>/           # project-specific guards and failures
│   │   ├── observations/
│   │   ├── profile.yaml
│   │   └── archetype.md
│   └── ...
└── profile_history/         # versioned snapshots for rollback
```

## Clean uninstall

```bash
synapptic uninstall          # removes skill, hook, settings entry, generated files
                             # asks before deleting your accumulated data
pip uninstall synapptic
```

synapptic only touches files it created. Your CLAUDE.md, .cursorrules, and other manually-written config files are never modified.

## Privacy

You choose where your data goes.

- **100% local option.** Use Ollama or LM Studio and nothing leaves your machine. No API keys, no cloud, no network calls. Your transcripts, profile, and observations stay on your disk.
- **Cloud option.** If you use Anthropic or OpenAI, filtered conversation text is sent to their API for analysis. Tool output and file contents are stripped by the filter, but your actual messages and the AI's responses are sent. If that's a concern, use a local model.
- **No telemetry.** synapptic has no analytics, no tracking, no phone-home. It talks to the LLM you configure and nothing else.

## Processing large session histories

If you have hundreds of sessions to process, use `--limit` to batch them:

```bash
synapptic update --limit 10    # process 10 sessions, merge, synthesize
synapptic update --limit 20    # next batch
synapptic update               # or just run them all (takes a while)
```

Each session takes 30-60 seconds to extract. synapptic shows progress as it goes and picks up where it left off if interrupted.

## Beta notice

synapptic is in active development. It works and is being used daily, but you should know:

- **LLM extraction is not deterministic.** The same session can produce slightly different observations on different runs. The weighted merge smooths this out over time, but individual observations may vary.
- **Profile quality depends on your LLM.** Local models (Ollama, LM Studio) are free but may produce lower quality extractions than cloud models. Start with a cloud provider and switch to local once you're happy with the results.
- **Large session backlogs take time.** If you have hundreds of sessions, process them in batches with `--limit`. The profile stabilizes after 10-20 sessions - you don't need to process everything.
- **The observation format may change** between versions. Your raw session transcripts are never modified, so you can always re-extract with a newer version.

Found a bug or have a suggestion? [Open an issue](https://github.com/appcuarium/synapptic/issues).

## Requirements

- Python 3.10+
- One LLM provider (Claude CLI, API key, or local model)
- That's it. Two dependencies installed automatically (click, pyyaml).

## License

MIT
