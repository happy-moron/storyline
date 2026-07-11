# Benchmark Overview

The benchmark evaluates LLM prompt performance for two core pipeline steps —
**translate** and **tokenize** — measuring both output quality and server-side cost
(token counts, timing).

It runs the eval set against a local LLM, validates structured outputs, runs a
remote LLM translation-quality check, and parses the local LLM's journalctl logs
to correlate wall-clock calls with server-side metrics.

---

## What It Measures

| Step | Prompt | Input | Quality Check | Cost Source |
|---|---|---|---|---|
| **translate** | `prompts/translate.md` | English source blocks | Remote LLM (`check_translation_blind.txt`) + mechanical checks | journalctl logs |
| **tokenize** | `prompts/tokenize.txt` | Chinese sentences (punctuation-stripped) | LCS-based comparison against golden tokenizations | journalctl logs |

### Translate quality

- **Mechanical**: parseable output, Chinese characters present, no refusals. Sentence-count
  mismatches are scored via a **completeness score** (`produced / expected`, 0–1) rather than
  treated as a hard failure — incomplete translations still pass validation and participate
  in the blind quality check.
- **Blind check**: all translated sentences across all valid blocks are concatenated and sent
  to a remote LLM (`glm-4.7-flash` by default) which checks for grammar/style errors without
  seeing the English source.

### Tokenize quality

An LCS-based algorithm aligns candidate vs. golden tokenized sentences, then aligns tokens
within matched sentences (see `plans/token_comparison_design.md`). It outputs:

- missing/extra sentences
- missing/extra tokens
- flawless sentences
- token error rate (`total_token_errors / total_reference_tokens`)

For blocks with multiple golden tokenization variants (e.g. `block_05_golden_tokenization.txt`
plus `_1`, `_2` variants), calibration runs the same comparison (golden vs. each variant) and
records the **worst** variant's error metrics as a natural-variance baseline.

### Cost (server-side)

The local LLM's journalctl logs are parsed post-run to extract per-request:
prompt/eval tokens, prompt/eval time (ms), total time, and tokens/sec.

---

## Key Files

### Source: `src/learnbook/benchmark/`

| File | Purpose |
|---|---|
| `__init__.py` | Shared data model dataclasses (`TaskResult`, `TranslateValidation`, `TokenizeValidation`) |
| `runner.py` | CLI entrypoint + orchestration: phases, warmup, log reconciliation, reporting |
| `token_compare.py` | LCS-based golden tokenization comparison (`compare_files()`) |
| `translate_compare.py` | Translation aggregation, remote LLM check call, error parsing |
| `llamacpp_log_parser.py` | journalctl log fetch & parse (legacy + native formats), time-window query |

### Prompt files

| File | Used by | Purpose |
|---|---|---|
| `prompts/translate.md` | translate phase | Translate English → Chinese (one sentence per line) |
| `prompts/tokenize.txt` | tokenize phase | Tokenize Chinese sentences → compact pipe format |
| `prompts/check_translation_blind.txt` | translate check | Remote LLM checks Chinese for grammar/style errors |
| `prompts/warmup.txt` | warmup | Single call to compile CUDA graphs before measurement |

### Eval set: `eval/`

| File pattern | Purpose |
|---|---|
| `block_0N.txt` | English source text (one sentence per line) |
| `block_0N_golden_translation.txt` | Reference Chinese translation (feeds tokenize phase) |
| `block_0N_golden_tokenization.txt` | Reference tokenization in compact pipe format |
| `block_0N_golden_tokenization_{1,2}.txt` | Alternate references for calibration |

### Config: `src/learnbook/config/llms_for_tasks.toml`

```toml
[llm]
provider = "local"
model_name = "local-llamacpp"
model_id = "local-llamacpp"

[default]
profile = "qwen36-35b-a3b-nothink"

[translate]
profile = "qwen36-35b-a3b-nothink"

[tokenize]
profile = "qwen36-35b-a3b-nothink"

[check_translation]
models = ["glm-4.7-flash"]
provider = "remote"

[benchmark]
repeat = 1
```

Translate/tokenize models are specified as llama.cpp **profiles**; the runner restarts
the local LLM service with the correct profile when needed. The check-translation model
runs on a remote provider. `--translate-models`, `--tokenize-models`, and
`--check-translation-models` flags override the config.

### Tests: `tests/learnbook/benchmark/`

| File | Tests |
|---|---|
| `test_runner.py` | Runner orchestration, validation functions, aggregate flows |
| `test_token_compare.py` | LCS comparison, all design-doc test scenarios |
| `test_translate_compare.py` | Aggregation, error parsing, block partitioning |
| `test_llamacpp_log_parser.py` | Log parsing for both legacy and native formats |

---

## Execution Flow

1. **Warmup** — one call with `prompts/warmup.txt` (excluded from reports).
2. **Translate phase** — sequentially run each English block through `prompts/translate.md`.
   Records wall-clock start/end per call.
3. **Tokenize phase** — strip CJK punctuation from golden translations, then sequentially
   run each through `prompts/tokenize.txt`. If the tokenize model differs from translate,
   the LLM service is restarted with the correct profile.
4. **Log reconciliation** — fetch journalctl logs spanning the full run window, parse
   into per-request records, and match them to results by wall-clock timestamp
   (with sequential fallback for native-format logs).
5. **Translation check** — stops the local LLM, fires one remote call to
   `prompts/check_translation_blind.txt` with all concatenated translated sentences,
   then partitions errors back to source blocks.
6. **Report** — prints a table summarizing each run plus calibration results.
   Optionally writes full JSON (`--output results.json`).

---

## CLI

```bash
# Full run with default models from config
python -m learnbook.benchmark.runner

# Override models
python -m learnbook.benchmark.runner --translate-models qwen36-35b-a3b
python -m learnbook.benchmark.runner --tokenize-models qwen36-35b-a3b
python -m learnbook.benchmark.runner --check-translation-models openrouter/deepseek/deepseek-v4-pro

# Quick smoke test (1 attempt, block_01 only)
python -m learnbook.benchmark.runner --smoke

# Multiple attempts per block
python -m learnbook.benchmark.runner --attempts 3

# Repeat the entire benchmark N times (aggregates results across runs)
python -m learnbook.benchmark.runner --repeat 3

# Output full results to JSON
python -m learnbook.benchmark.runner --output results.json

# Parse logs only (no LLM calls)
python -m learnbook.benchmark.runner --no-run --since "1 hour ago"

# Skip log parsing (LLM calls only, no server-side cost data)
python -m learnbook.benchmark.runner --no-parse-logs
```

### Options

| Flag | Default | Description |
|---|---|---|
| `--translate-models` | from config | Comma-separated model names |
| `--tokenize-models` | from config | Comma-separated model names |
| `--check-translation-models` | from config | Comma-separated model names |
| `--smoke` | off | 1 attempt, block_01 only |
| `--attempts N` | 1 | Attempts per block |
| `--repeat N` | 1 (from config) | Full benchmark repetitions (aggregated) |
| `--output PATH` | none | Write full JSON results to PATH |
| `--parse-logs` / `--no-parse-logs` | `--parse-logs` | Fetch server-side metrics from journalctl |
| `--no-run` | off | Parse logs only (requires `--since`) |
| `--since` | — | journalctl `--since` string (used with `--no-run`) |

### `llamacpp_log_parser` standalone CLI

The log parser also works standalone:

```bash
# Summary of recent requests
python -m learnbook.benchmark.llamacpp_log_parser --lines 200

# Time-window query
python -m learnbook.benchmark.llamacpp_log_parser --since "1 hour ago"

# Output to JSON or CSV
python -m learnbook.benchmark.llamacpp_log_parser --since "1 hour ago" --output-json out.json
python -m learnbook.benchmark.llamacpp_log_parser --since "1 hour ago" --output-csv out.csv

# Parse a saved log file
python -m learnbook.benchmark.llamacpp_log_parser --input-file mylog.txt
```