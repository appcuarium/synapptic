# Ollama Setup Guide for Synapptic

## Quick Start

```bash
# 1. Select Ollama provider with qwen3-coder-next model
synapptic config provider
# → Select option 4 (Ollama)
# → Select option 1 (qwen3-coder-next:q4_K_M)
# → Press enter for API URL (default: http://localhost:11434/v1)

# 2. Configure context and GPU settings
synapptic config ollama
# → Select preset (1=max/256k, 2=extraction/131k, 3=synthesis/64k, 4=benchmark/64k)
# → Set GPU offload to 99 (recommended for M2 Max)

# 3. Verify config
synapptic config show
```

## Context Presets

| Preset | Context | Temperature | Use Case |
|--------|---------|-------------|----------|
| **max** | 256k | 0.1 | Large-scale extraction (entire codebases) |
| **extraction** | 131k | 0.1 | Session transcript extraction, detailed analysis |
| **synthesis** | 64k | 0.3 | Archetype generation from profiles |
| **benchmark** | 64k | 0.2 | Guard test generation & execution |

- **Max (256k)**: Best for processing entire projects or very long transcripts
- **Extraction (131k)**: Balanced for typical session transcripts with full context
- **Synthesis (64k)**: Sufficient for profile→archetype conversion
- **Benchmark (64k)**: Testing behavior patterns, deterministic scoring

## How to Use

### Extract observations from sessions with max context:
```bash
synapptic config ollama  # Select "max" preset
synapptic extract -s <session-uuid>
```

### Run benchmarks with Qwen3-Coder:
```bash
synapptic config ollama  # Select "benchmark" preset (default)
synapptic benchmark -p <project> -n 15 --seed 123
```

### Compare models/providers:
```bash
synapptic results list
synapptic results compare claude-cli sonnet ollama qwen3-coder-next:q4_K_M
```

## Ollama Native API Parameters

Synapptic automatically sends these parameters to Ollama's `/api/generate` endpoint:

```json
{
  "model": "qwen3-coder-next:q4_K_M",
  "prompt": "...",
  "stream": false,
  "options": {
    "num_ctx": 262144,        // Context window (configurable)
    "num_gpu": 99,            // GPU layer offload (configurable)
    "temperature": 0.1,       // Randomness (configurable per preset)
    "top_p": 0.95             // Nucleus sampling (configurable per preset)
  }
}
```

## Tool Calling & Prompt Adaptation

### Current Status
- **Extraction**: ✅ Works as-is (text-only, no tools needed)
- **Synthesis**: ✅ Works as-is (text-only, no tools needed)
- **Benchmarking**: ✅ Works as-is (regex-based, no tools needed)
- **Tool calling**: ⚠️ Not enabled (future enhancement)

Synapptic's core operations (extraction, synthesis, benchmarking) use **text-only prompts** with regex-based validation. Qwen3-Coder handles these natively without requiring Claude's tool calling format.

### Why No Tool Calling Now?
1. Synapptic's tool-use (if implemented) would be for internal functions (e.g., "run_regex_test")
2. Qwen3 doesn't support Claude-style tool definitions natively
3. Text-only extraction is sufficient for current use cases
4. Future work: Could add XML-based tool definitions for Ollama if needed

### Prompt Characteristics
Synapptic prompts are tuned for **deterministic, extraction-focused behavior**:
- Low temperature (0.1-0.3) → Consistent outputs
- Focus on rules, patterns, evidence → Qwen3 handles well
- Examples include both positive/negative cases → Good for instruction-following
- YAML/JSON structured output → Qwen3 reliable with format spec

**Qwen3-Coder is optimized for code/logic problems**, so these instruction-following prompts work well. Results may differ slightly from Claude due to different training, but quality is comparable for extraction/synthesis tasks.

## Performance Expectations

### M2 Max (96GB) with Qwen3-Coder

| Operation | Time | Notes |
|-----------|------|-------|
| Extract observations | 10-30s | 256k context, full transcript |
| Generate test cases | 5-15s | Per test case, per run |
| Synthesize archetype | 20-40s | From weighted profile |
| Benchmark run | 2-5min | 15 guards × 3 runs |

Time varies by:
- Context window size (larger = slower)
- Model quantization (q4_K_M is faster than full precision)
- Transcript complexity
- Available GPU layers (num_gpu=99 recommended)

## Troubleshooting

### "Ollama API error: Connection refused"
```bash
# Ensure Ollama server is running
ollama serve

# In another terminal, verify qwen3 is available:
ollama list | grep qwen3
```

### "Model not found: qwen3-coder-next:q4_K_M"
```bash
# Pull the model first:
ollama pull qwen3-coder-next:q4_K_M
```

### Slow responses (>30s per extraction)
```bash
# Check available context vs configured:
synapptic config show

# Reduce context if needed:
synapptic config ollama  # Select "extraction" instead of "max"

# Or check GPU offload:
ollama list | grep qwen3  # Verify loaded in memory
```

### Inconsistent extraction results
- Qwen3's behavior varies more at higher temperatures
- Use `synapptic config ollama` → select "extraction" (temp=0.1) for consistency
- Run benchmark multiple times with `--seed` to measure variance

## Benchmark Comparison

Compare results across models/providers:

```bash
# First run: seed selects guards deterministically, test cases cached per seed+model
synapptic benchmark -p machine-be -n 15 --seed 123
# → Seed 123 selects 15 guards from profile (deterministic)
# → Caches test cases: machine-be_tests_seed123_qwen3-coder-next_q4_K_M.json
# → Saves results with provider/model metadata

# Same seed+model: reuses cached tests, new result timestamp
synapptic benchmark -p machine-be -n 15 --seed 123
# → Reuses cached tests (seed is a cache key, not a determinism guarantee)
# → Different result file (timestamp differs)

# Different model: different test cases generated
synapptic benchmark -p machine-be -n 15 --seed 123 --model sonnet
# → New test cache: machine-be_tests_seed123_sonnet.json
# → New results

# Clear test case cache (keep results)
synapptic benchmark --seed 123 --flush-tests
# → Regenerates tests for all models with seed 123

# Clear everything
synapptic benchmark --flush-all
# → Deletes all test cases AND results (fresh start)

# View all saved results
synapptic results list

# Compare two models
synapptic results compare claude-cli sonnet ollama qwen3-coder-next:q4_K_M
```

## Advanced: Custom Presets

For custom configurations not covered by presets:

```bash
synapptic config ollama
# → Select option 5 (custom)
# → Enter custom num_ctx, temperature, top_p
```

This creates a "custom" preset in config for future reuse.

## Integration with Claude Code

To use Ollama with Claude Code locally:

```bash
CLAUDE_CODE_BASE_URL="http://localhost:11434/v1" \
CLAUDE_CODE_MODEL="qwen3-coder-next:q4_K_M" \
claude
```

Note: Claude Code's built-in agents may work slightly differently with local models due to assumption of Anthropic-specific XML formatting.
