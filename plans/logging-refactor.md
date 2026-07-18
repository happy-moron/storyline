# Logging Refactor — Structured Pipeline Logging

## Motivation

The current logging setup has several gaps:

- **Console-only** — No file persistence; all logs go to stdout via `logging.basicConfig()`.
- **Unstructured format** — `HH:MM:SS <message>` with no key=value pairs, making programmatic analysis and grep filtering difficult.
- **No performance metrics** — LLM calls log only character counts (`"Sending prompt ... (%d chars)"` / `"Response received (%d chars)"`). No wall-clock timing, no server-side token counts, no tokens/sec.
- **No audio performance logging** — TTS generation, forced alignment, and aggregate audiobook build have no timing or structured events.
- **No run correlation** — Multiple runs are indistinguishable in logs. No `run_id` to filter one run's output.
- **Noisy subprocess output** — `soundstretch` is called without `capture_output`, dumping verbose progress lines to the console.
- **Rich data goes unused** — The llamacpp server logs (journalctl) contain detailed per-request metrics (prompt/eval tokens, timing, cache hit info) and the benchmark already parses them successfully (`src/storyline/benchmark/llamacpp_log_parser.py`), but this data never surfaces in pipeline logs.

## Goals

1. **File-based logging** — Important server logs are persisted to `logs/` in the project root.
2. **Structured key=value format** — Every log line includes a standard prefix (`run_id`, `logger`, timestamp, level) and follow a `key=value` convention in the message body.
3. **Run ID** — Each run of the pipeline generates a unique `run_id` injected into every log line, making it easy to filter one run:
   ```bash
   grep "run_id=abc123" logs/server.log
   ```
4. **LLM performance metrics in pipeline logs** — After each LLM call, query journalctl to extract server-side token/timing data and log it.
5. **Audio generation metrics** — Structured events for TTS generation, forced alignment, and audiobook assembly.
6. **Silence soundstretch** — Capture stdout/stderr, suppressing verbose progress output.
7. **Backward compatible** — Modules not yet migrated continue to work; changes are phased incrementally.

## Design

### 1. Centralized Logging — `src/storyline/logging.py`

A new module that replaces scattered `logging.basicConfig()` / `logging.getLogger(__name__)` calls across the project.

```python
# src/storyline/logging.py

import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from logging.handlers import RotatingFileHandler

_run_id: str | None = None
_log_dir: Path = Path("logs")

class _RunIdFilter(logging.Filter):
    def filter(self, record):
        record.run_id = _run_id or "-"
        return True

def init(run_id: str | None = None, log_dir: str | Path = "logs") -> str:
    """Configure the root 'storyline' logger with console + file handlers.

    Returns the run_id used (generated if none provided).
    """
    global _run_id, _log_dir
    _run_id = run_id or datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("storyline")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-5s run_id=%(run_id)s %(name)s %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Console: INFO+ only
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # File: DEBUG+ with rotation (for full trace)
    log_path = _log_dir / f"server_{_run_id}.log"
    file_handler = RotatingFileHandler(log_path, maxBytes=10_000_000, backupCount=5)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # Symlink for convenience: logs/server.log -> latest run
    latest = _log_dir / "server.log"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(log_path.name)

    return _run_id

def get_logger(name: str) -> logging.Logger:
    """Return a child logger under 'storyline'."""
    return logging.getLogger(f"storyline.{name}")
```

**Key design decisions:**

- One call to `storyline.logging.init(run_id="...")` at the top of `create_book()`. All subsequent module loggers are children of `"storyline"`.
- `run_id` is injected via a `logging.Filter` — zero code change at individual log call sites.
- `logs/server.log` is a symlink to the current run's log file (convenient for `tail -f logs/server.log`).
- `RotatingFileHandler` with 10 MB / 5 backups prevents unbounded growth. Per-run files are already named with the run_id for archiving.
- The formatter uses ISO-8601 timestamps (`%Y-%m-%dT%H:%M:%S`) for sortability.

### 2. Structured Log Message Convention

All messages follow a `key=value key=value` convention. The formatter already adds timestamp, level, run_id, and logger name. Example:

```
log.info("event=llm_call prompt=translate chapter=%s chars_in=%d wall_ms=%d "
         "prompt_tok=%d eval_tok=%d prompt_tps=%.1f eval_tps=%.1f server_total_ms=%d cache=%s",
         chapter, chars, wall_ms, prompt_tok, eval_tok, prompt_tps, eval_tps, total_ms, cache)
```

This outputs:

```
2026-07-18T14:30:50 INFO  run_id=20260718_143022_a1b2c3d4 storyline.prompt_utils event=llm_call prompt=translate chapter=myslug_1 chars_in=1847 wall_ms=45230 prompt_tok=1234 eval_tok=189 prompt_tps=27.3 eval_tps=15.2 server_total_ms=45190 cache=LRU
```

Grep-friendly:

```bash
grep "event=llm_call" logs/server.log                           # all LLM calls
grep "event=llm_call.*chapter=myslug_1" logs/server.log         # LLM calls for one chapter
grep "run_id=abc123.*event=pipeline_step" logs/server.log       # step timings for one run
```

### 3. Run ID Generation

`run_id` is generated in `create_book()` at startup:

```
run_id = logging.init(run_id=f"{book}_{timestamp}")   # e.g. "myslug_20260718_143022_a1b2c3d4"
```

The run_id includes the book slug for human readability plus a short UUID for uniqueness. This is passed to `storyline.logging.init()`.

### 4. Pipeline Step Events

Each pipeline step in `create_book()` gets timing. Standard events:

```
event=pipeline_start   book=B author=A profile=P
event=pipeline_step    step=split      chapter=N chars=C duration_ms=D
event=pipeline_step    step=simplify   chapter=N chars_in=C chars_out=O duration_ms=D attempt=A
event=pipeline_step    step=chunk      chapter=N lines=L nchunks=C duration_ms=D
event=pipeline_step    step=translate  chapter=N pairs=P duration_ms=D attempt=A
event=pipeline_step    step=tokenize   chapter=N sentences=S retries=R duration_ms=D
event=pipeline_step    step=audio      chapter=N chunks=C duration_ms=D
event=pipeline_step    step=dict       chapter=N words=W duration_ms=D
event=book_complete    book=B author=A chapters=N total_duration_ms=D
```

Implementation: simple `t0 = time.time(); ...; duration_ms = (time.time() - t0) * 1000; log.info(...)` at each step boundary. No heavy context-manager abstraction required.

### 5. LLM Server-Side Metrics (journalctl Integration)

**Problem:** `run_prompt()` uses `zsp_llm_client.PromptRunner` which returns only raw text — no timing or token information.

**Solution:** After each LLM call, query journalctl for the matching llamacpp log entry using the existing `parse_window()` function from `llamacpp_log_parser.py`. This extracts per-request: prompt tokens, eval tokens, prompt tps, eval tps, total time, cache selection method, etc.

New wrapper in `run_prompt.py`:

```python
def run_prompt_with_metrics(
    prompt_template_path, input_file_path, output_file_path,
    models=None, timeout=600,
) -> dict:
    """Run a prompt and return a metrics dict alongside writing output."""
    from datetime import datetime, timezone, timedelta
    from storyline.benchmark.llamacpp_log_parser import parse_window

    wall_start = datetime.now(timezone.utc)
    run_prompt(prompt_template_path, input_file_path, output_file_path,
               models=models, timeout=timeout)
    wall_end = datetime.now(timezone.utc)

    metrics = {"wall_ms": int((wall_end - wall_start).total_seconds() * 1000)}

    try:
        records = parse_window(
            start=wall_start - timedelta(seconds=2),
            end=wall_end + timedelta(seconds=5),
        )
        meaningful = [r for r in records
                      if r.total_tokens is not None and r.total_tokens > 50]
        if meaningful:
            rec = meaningful[-1]
            metrics.update({
                "prompt_tokens": rec.prompt_eval_tokens,
                "eval_tokens": rec.eval_tokens,
                "prompt_tps": rec.prompt_eval_tokens_per_sec,
                "eval_tps": rec.eval_tokens_per_sec,
                "server_total_ms": rec.total_time_ms,
                "cache": rec.cache_selected_by,
                "model_hint": rec.model_hint,
            })
    except Exception:
        pass  # journalctl unavailable — metrics limited to wall_ms only

    return metrics
```

`create_book.py` calls `run_prompt_with_metrics()` for every pipeline step that uses an LLM (simplify, chunk, translate, tokenize, dictionary), and logs one structured line per call.

**Trade-off:** Querying journalctl after each call adds a small subprocess overhead (~100 ms). In practice LLM calls take seconds-to-minutes, so this is negligible. If it ever becomes problematic, it can be gated behind a config flag.

### 6. Audio Generation Logging

In `process_chapter_chunks()` and the TTS service:

```
event=audio_chunk    chapter=N chunk=I/I lang=zh chars=C duration_ms=D
event=audio_align    chapter=N chunk=I/I words=W duration_ms=D
event=audio_aggregate chapter=N chunks=C segments=S duration_ms=D total_s=S
```

In `change_tempo()` (at DEBUG level only — these fire frequently):

```
event=tempo speed=0.8x input_ms=D output_ms=D
```

### 7. Silencing soundstretch

In `audiobook_gen_base.py:change_tempo()`, add `capture_output=True` to the subprocess call:

```python
subprocess.run([
    "soundstretch", input_path, output_path,
    f"-tempo={int((speed_change-1)*100)}"
], check=True, capture_output=True)
```

This swallows soundstretch's verbose progress output. Stderr is available via the `CompletedProcess` object if debugging is needed.

### 8. Service Manager Events

`ServiceManager` switches to `storyline.logging.get_logger("services")`. Structured events:

```
event=service_start   service=llm profile=qwen36-35b-a3b
event=service_stop    service=llm
event=service_switch  from=llm to=tts gpu_wait_ms=D
event=service_health  service=llm status=healthy
event=service_health  service=llm status=timeout elapsed_ms=D
```

Also replace `print()` calls (e.g., "Waiting for ... to become healthy") with `log.info()` using structured format.

### 9. Output Layout

```
storyline/
├── logs/
│   ├── server_20260718_143022_a1b2c3d4.log   # full per-run log
│   ├── server.log -> server_20260718_143022_a1b2c3d4.log  # symlink to latest
│   └── ...
└── eval/logs/
    └── benchmark_runs.csv                     # already exists, unchanged
```

`.gitignore` updated to add `logs/`.

### 10. Target Log Output Example

```
2026-07-18T14:30:22 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=pipeline_start book=myslug author=myauthor profile=default
2026-07-18T14:30:22 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.services event=service_start service=llm profile=qwen36-35b-a3b
2026-07-18T14:30:45 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.services event=service_health service=llm status=healthy
2026-07-18T14:30:45 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=pipeline_step step=split chapter=myslug_1 chars=1847 duration_ms=12
2026-07-18T14:30:50 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=pipeline_step step=simplify chapter=myslug_1 chars_in=1847 chars_out=1562 duration_ms=45230 attempt=1
2026-07-18T14:30:50 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.prompt_utils event=llm_call prompt=simplify chapter=myslug_1 chars_in=1847 wall_ms=45230 prompt_tok=1234 eval_tok=189 prompt_tps=27.3 eval_tps=15.2 server_total_ms=45190 cache=LRU model=qwen36-35b-a3b
2026-07-18T14:31:01 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=pipeline_step step=chunk chapter=myslug_1 lines=23 nchunks=3 duration_ms=11200
2026-07-18T14:31:01 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.prompt_utils event=llm_call prompt=chunk chapter=myslug_1 chars_in=1562 wall_ms=11190 prompt_tok=567 eval_tok=89 prompt_tps=50.7 eval_tps=38.1 server_total_ms=11150 cache=LRU model=qwen36-35b-a3b
2026-07-18T14:31:50 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=pipeline_step step=translate chapter=myslug_1 pairs=23 duration_ms=48760 attempt=1
2026-07-18T14:31:50 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.prompt_utils event=llm_call prompt=translate chapter=myslug_1 chars_in=1562 wall_ms=48730 prompt_tok=1102 eval_tok=234 prompt_tps=22.6 eval_tps=14.8 server_total_ms=48680 cache=LRU model=qwen36-35b-a3b
2026-07-18T14:32:15 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=pipeline_step step=tokenize chapter=myslug_1 sentences=23 retries=0 duration_ms=24810
2026-07-18T14:32:15 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.prompt_utils event=llm_call prompt=tokenize chapter=myslug_1 chars_in=345 wall_ms=24780 prompt_tok=456 eval_tok=301 prompt_tps=18.4 eval_tps=12.2 server_total_ms=24740 cache=LRU model=qwen36-35b-a3b
2026-07-18T14:32:18 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.services event=service_switch from=llm to=tts gpu_wait_ms=2500
2026-07-18T14:32:18 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.services event=service_health service=tts status=healthy
2026-07-18T14:32:23 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.audio event=audio_chunk chapter=myslug_1 chunk=1/3 lang=zh chars=87 duration_ms=4200
2026-07-18T14:32:25 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.audio event=audio_align chapter=myslug_1 chunk=1/3 words=31 duration_ms=1800
2026-07-18T14:32:28 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.audio event=audio_chunk chapter=myslug_1 chunk=2/3 lang=zh chars=94 duration_ms=4600
2026-07-18T14:32:30 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.audio event=audio_align chapter=myslug_1 chunk=2/3 words=34 duration_ms=1900
...
2026-07-18T14:33:30 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.audio event=audio_aggregate chapter=myslug_1 chunks=3 segments=69 duration_ms=5800 total_s=218.3
2026-07-18T14:33:30 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.services event=service_switch from=tts to=llm gpu_wait_ms=3100
2026-07-18T14:33:55 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=pipeline_step step=dict chapter=myslug_1 words=45 duration_ms=24900
...
2026-07-18T14:45:00 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.book event=book_complete book=myslug author=myauthor chapters=12 total_duration_ms=848000
2026-07-18T14:45:00 INFO  run_id=myslug_20260718_143022_a1b2c3d4 storyline.services event=service_stop service=llm
```

### 11. Files to Create / Modify

| File | Action | Phase |
|---|---|---|
| `src/storyline/logging.py` | **New** — centralized logging setup | 1 |
| `.gitignore` | Add `logs/` | 1 |
| `src/storyline/book/create_book.py` | Call `logging.init()`, structured events, timing, `run_prompt_with_metrics()` | 1, 2, 3 |
| `src/storyline/prompt_utils/run_prompt.py` | Add `run_prompt_with_metrics()` wrapper | 3 |
| `src/storyline/services/manager.py` | Use `get_logger()`, structured events, replace `print()` | 1 |
| `src/storyline/audio/audiobook_gen_base.py` | `capture_output=True` on soundstretch | 4 |
| `src/storyline/audio/audiobook_gen_chunk.py` | Structured events for chunks, alignment, aggregate | 4 |
| `src/storyline/audio/audiobook_gen_qwen3.py` | Structured events for TTS/alignment calls | 4 |
| `src/storyline/book/parse_chunk_format.py` | Use `get_logger()` | 5 |
| `src/storyline/book/check_tokenization.py` | Use `get_logger()` | 5 |

### 12. Rollout Phases

Changes are ordered so each phase can be reviewed, tested, and merged independently:

1. **Phase 1 — Infrastructure**
   - Add `src/storyline/logging.py`
   - Update `.gitignore`
   - Wire into `create_book.py` (replace `logging.basicConfig` with `logging.init()`)
   - Migrate `services/manager.py` to use `get_logger()` and structured events
   - Replace `manager.py` `print()` calls with `log.info()`
   - All other modules use root logger unchanged (backward compatible)

2. **Phase 2 — Pipeline Timing**
   - Add timing wrappers and structured `event=pipeline_step` log lines in `create_book.py`
   - Add `event=pipeline_start` / `event=book_complete` at boundaries

3. **Phase 3 — LLM Metrics**
   - Add `run_prompt_with_metrics()` in `prompt_utils/run_prompt.py`
   - Wire into `create_book.py` for simplify, chunk, translate, tokenize, and dictionary LLM calls
   - Log `event=llm_call` with journalctl-derived metrics

4. **Phase 4 — Audio + soundstretch**
   - Add `capture_output=True` in `audiobook_gen_base.py:change_tempo()`
   - Add structured `event=audio_*` log lines in `audiobook_gen_chunk.py`
   - Add structured events in `audiobook_gen_qwen3.py` for TTS/alignment calls

5. **Phase 5 — Remaining Modules**
   - Migrate `parse_chunk_format.py`, `check_tokenization.py` to `get_logger()`
   - Ensure all `logging.getLogger(__name__)` in `src/storyline/` use `get_logger()`

### 13. Non-Goals

- **Not changing the benchmark runner** — The benchmark already has its own structured output (CSV, JSON) and log parsing. It can optionally adopt the new `logging.init()` in a future change.
- **Not modifying external dependencies** — `zsp_llm_client.PromptRunner` is left untouched. We wrap around it rather than modifying it.
- **No structured logging library** (e.g., `structlog`) — A dependency-free approach using only stdlib `logging` keeps things simple and avoids coupling to a specific framework. The `key=value` convention is sufficient for grep/awk/jq-based analysis.
- **No centralized log aggregation** — This is a single-workstation project. Logs are local files.

### 14. Future Enhancements (Out of Scope)

- Per-run log analysis script (parse `logs/server*.log` to produce aggregate timing reports — like the benchmark runner's report but for full pipeline runs)
- Integration of `eval/logs/benchmark_runs.csv` into the same structured log format
- Configurable log levels per module via `pipeline.toml`
- TTS server-side metrics (currently no equivalent of `llamacpp_log_parser` exists for the TTS Flask service)