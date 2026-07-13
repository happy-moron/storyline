# Book Creation Pipeline Walkthrough

## Entry Point

`src/storyline/book/create_book.py` — invoked via CLI or imported programmatically. The `create_book()` function orchestrates the full pipeline; the `__main__` block parses CLI args, loads config, and calls `create_book()`.

### CLI Arguments

| Flag | Default | Description |
|---|---|---|
| `-i` / `--input` | *(required)* | Plaintext book file path |
| `-a` / `--author` | *(required)* | Author name (used in output path) |
| `--profile` | `None` | Named profile from `pipeline.toml` (e.g. `quick`, `test`) |
| `-m` / `--models` | from config | Comma-separated model list (overrides config file) |
| `--max-chunks` | `500` | Max number of chapters to process |
| `--skip-simplify` | `False` | Skip the simplification step |
| `--skip-audio` | `False` | Skip audio generation |
| `--audio-profile` | `"default"` | Audio profile from `audio.toml` (e.g. `voice-clone`) |

---

## Terminology

The pipeline uses these terms consistently across code, prompts, and the reader:

| Term | Definition |
|---|---|
| **Chapter** | A ~2000-character text block produced by the split step. Numbered sequentially (`{prefix}_{N}`). |
| **Chunk** | A natural-reading grouping of lines within a chapter, annotated with an `@instruct` voice direction. |
| **Line** | A single sentence. One line per sentence in source text; translated output preserves the 1:1 mapping. |

---

## Pipeline Steps

For a book by `{author}` named `{book}`, all output lands under `books/{author}/{book}/`.

```
books/{author}/{book}/
├── split/
│   ├── source/        # Step 1: chapter-sized raw text blocks
│   └── simple/        # Step 2: simplified English (optional)
├── chunks/            # Step 3 + Step 6: chunk metadata + per-chunk audio
│   ├── {prefix}_{N}.json
│   ├── {prefix}_{N}_zh_00.mp3
│   └── {prefix}_{N}_en_00.mp3
├── pipe/
│   ├── source/        # Step 4: Chinese ||| English backtranslation
│   └── tokenized/     # Step 5: POS-annotated compact pipe format
└── audio/             # Fallback: per-sentence MP3s (sentence-based flow)
```

Dictionary updates go to `dict/custom_dict.json` (Step 7).

---

### Step 1 — Text Splitting

**Module:** `src/storyline/book/split_text.py`

The input plaintext book is split on paragraph boundaries (`\n\n+`). Paragraphs are accumulated into chapters of ≤ ~2000 characters. Each chapter is written as `{book}_{N}.txt` under `split/source/`, where `N` is a sequential number starting at 1.

Files are processed in natural numeric order (e.g. `1, 2, 10, 11`, not `1, 10, 11, 2`).

---

### Step 2 — Simplify English (Optional)

**Module:** `src/storyline/prompt_utils/run_prompt.py`
**Prompt:** `prompts/simplify.md`
**Default model:** configured in `llms_for_tasks.toml` (`qwen36-35b-a3b`)

Each chapter is sent to the LLM to simplify the English. The prompt instructs:
- Target ~9th grade reading level
- Reduce complex grammar, replace rare words with common ones
- Preserve proper nouns and technical terms
- Output one or two related sentences per line
- No markdown, no explanations

Output: `split/simple/{N}.txt`

If `--skip-simplify` is set, this step is skipped and subsequent stages use the raw source text. This is typical for books already written at an appropriate reading level.

---

### Step 3 — Chunking

**Module:** `src/storyline/book/parse_chunk_format.py`
**Prompt:** `prompts/tts_chunking_prompt_single_narrator.md`

The text (simplified or raw source) is sent to the LLM to identify natural reading chunks. The LLM outputs `@instruct` / `@previous` / `@next` blocks that mark where one chunk ends and the next begins. Chunks are contiguous ranges of source lines.

The parser (`parse_chunk_format.py`) maps these boundaries to line indices and validates:
- The first `@previous` is empty (marks start of text)
- All anchor lines exist in the source
- `@previous` and `@next` within each block are adjacent in the source

Output: `chunks/{N}.json` — a JSON file with `{"chunks": [...]}`. Each chunk has:
```json
{
  "instruct": "Frightened whisper",
  "line_range": [0, 4]
}
```

---

### Step 4 — Translate to Chinese

**Module:** `src/storyline/prompt_utils/run_prompt.py`
**Prompt:** `prompts/translate.md`

Each line of the source text (simplified or raw) is translated to a single Chinese line. The prompt outputs one Chinese sentence per line — the same number of lines as the input. The pipeline then pairs each Chinese line with its English source using the `|||` separator:

```
这只狗是棕色的。|||The dog is brown.
它喜欢吃骨头。|||It likes to eat bones.
```

Output: `pipe/source/{N}.txt` — one `chinese|||english` line per sentence.

---

### Step 5 — Tokenize & POS-Annotate Chinese

**Module:** `src/storyline/prompt_utils/run_prompt.py` then tokenization repair
**Prompt:** `prompts/tokenize.txt`

Chinese sentences are stripped of CJK punctuation, tokenized, and POS-annotated. Each token is output in compact pipe format:

```
Token|Pinyin|POS|Component~CompPinyin|...
```

Multiple tokens per line are separated by `||`:

```
这|zhè|r||只|zhǐ|q||狗|gǒu|n||是|shì|v||棕色|zōngsè|n|棕~zōng|色~sè||的|de|u
```

The tokenization step includes:
1. **CJK punctuation stripping** — punctuation is removed before tokenization and reinserted afterwards so that the tokenizer sees clean Chinese text
2. **Tokenization repair** — if the LLM output has errors (missing sentences, garbled lines) but is structurally valid, a targeted repair prompt (`prompts/fix_tokenization.txt`) fixes individual lines without requiring a full re-run
3. **Full retry** — if output is unparseable or has structural errors, the entire chapter is re-tried

Output: `pipe/tokenized/{N}.txt`

---

### Step 6 — Audio Generation (Chunk-Based)

**Modules:** `src/storyline/audio/audiobook_gen_chunk.py`, `audiobook_gen_qwen3.py`
**TTS endpoint:** `http://127.0.0.1:11433` (Qwen3-TTS Flask service)

Audio is generated per-chunk, not per-sentence. The flow:

1. **Per-chunk TTS** — each chunk's Chinese text and English text are sent to the TTS as whole blocks, passing the chunk's `@instruct` as voice direction for Chinese. English uses no instruct.
2. **Forced alignment** — each audio segment is sent to `/forced_aligner` to get word-level timestamps.
3. **Line boundary computation** — line boundaries are calculated as `(last_word_of_line.end_time + first_word_of_next.start_time) / 2`. First line starts at 0.0; last line ends at audio duration.
4. **Chunk metadata update** — the chunk JSON is updated with audio filenames, per-line timestamps, and word data.
5. **Aggregate audiobook** — sentence-length audio slices are extracted from chunk audio using line timestamps, speed-adjusted per the audio profile cadence, and assembled into a combined MP3.

**Audio profiles** (in `audio.toml`) define the repetition cadence. The default profile:

```
slow Chinese (0.8x)  →  English  →  medium Chinese (0.9x)  →  English  →  normal Chinese  →  English
```

Each segment is separated by a 300 ms pause.

**Outputs:**
- Per-chunk audio files: `chunks/{prefix}_{N}_zh_{NN}.mp3` and `chunks/{prefix}_{N}_en_{NN}.mp3`
- Chunk metadata: `chunks/{prefix}_{N}.json` (updated with timestamps)
- Aggregate audiobook: `/home/zspdude/temp/audio/{book}/{N}.mp3`

**Fallback:** If no chunk JSON exists for a chapter, the pipeline falls back to per-sentence audio generation (one MP3 per sentence, Chinese only, normal speed).

**Voice modes** (defined in `audio.toml`):
- **`custom_voice`** — calls `/custom_voice` with a Qwen3 pretrained speaker (e.g. Vivian for Chinese, Ryan for English) and optional `instruct` string
- **`voice_clone`** — calls `/voice_clone` with a reference audio file + transcript to clone a custom voice

The profile is selected via `--audio-profile` (defaults to `"default"` which uses Vivian/Ryan).

---

### Step 7 — Dictionary Building

**Module:** `src/storyline/book/create_custom_dict.py`
**Prompt:** `prompts/create_single_dictionary_entry.txt`

The tokenized pipe file is scanned for every Chinese word token. Any word that:
- Is not punctuation
- Is not already in `dict/custom_dict.json`
- Has non-empty simplified text

…is sent to the LLM to create a dictionary entry. The LLM returns JSON with:
- The word (simplified Chinese)
- Pinyin
- A list of meanings/senses, each with:
  - A meaning description
  - An example sentence (Chinese, pinyin, English translation)

The dictionary is saved after each chapter is processed.

---

## Service Orchestration

**Module:** `src/storyline/services/manager.py`

VRAM is limited — only one of the LLM, TTS, or Image-gen models can run at a time. The `ServiceManager` singleton manages mutual exclusion:

| Service | systemctl unit | Port | Health check |
|---|---|---|---|
| LLM | `llamacpp` | 11432 | `GET /v1/models` |
| TTS | `qwentts` | 11433 | `GET /health` |
| Image-gen | `qwen-image-gen` | 11434 | `GET /health` |

The TTS Flask server hosts both the TTS models (~3.4 GiB VRAM) and the forced aligner model (~1.7 GiB VRAM) in separate VRAM slots. The aligner can run without stopping TTS.

The pipeline lifecycle:
1. **Start:** LLM is started at the beginning of `create_book()`. An optional warmup prompt compiles CUDA graphs.
2. **During text stages (Steps 2–5, 7):** LLM stays running. Model profiles can be switched per-task via systemctl restart.
3. **During audio (Step 6):** The TTS service is started (implicitly stopping the LLM). After audio, the TTS is stopped and the LLM is restarted.
4. **End:** LLM is stopped in a `finally` block.

---

## Model Configuration

**File:** `src/storyline/config/llms_for_tasks.toml`

Each pipeline task can use a different llama.cpp profile:

```toml
[llm]
provider = "local"
model_name = "local-llamacpp"
model_id = "local-llamacpp"

[default]
profile = "qwen36-35b-a3b"

[translate]
profile = "qwen36-35b-a3b"

[tokenize]
profile = "qwen36-35b-a3b"

[dictionary]
profile = "qwen36-35b-a3b"
```

Task-specific profiles override the `[default]` profile. When a profile changes between tasks, the `ServiceManager` restarts the LLM service with the new profile.

---

## Config Files

| File | Purpose |
|---|---|
| `src/storyline/config/pipeline.toml` | Pipeline paths, behaviour flags, named profiles |
| `src/storyline/config/audio.toml` | Voice definitions, audio profiles, cadence sequences |
| `src/storyline/config/llms_for_tasks.toml` | LLM provider, task profiles |
| `src/storyline/config/services.toml` | Service ports, timeouts, health endpoints |

---

## Key Dependencies

| Component | Purpose |
|---|---|
| `zsp_llm_client.PromptRunner` | High-level LLM prompt execution with rate limiting |
| `requests` | HTTP client for TTS service |
| `pydub` | Audio segment manipulation (combining, tempo change, export) |
| `soundstretch` | CLI tool for tempo change without pitch alteration |
| `systemctl --user` | Service lifecycle (start/stop/health checks) |
| `tomllib` | TOML config parsing (Python 3.11+) |

---

## Quick Reference: Default Settings

| Setting | Default |
|---|---|
| Chapter max size | 2000 characters |
| Max chapters processed | 500 |
| Simplify model | `qwen36-35b-a3b` |
| Translate model | `qwen36-35b-a3b` |
| Tokenize model | `qwen36-35b-a3b` |
| Dictionary model | `qwen36-35b-a3b` |
| LLM endpoint | `http://127.0.0.1:11432/v1` |
| LLM request timeout | 600 s (10 min) |
| TTS endpoint | `http://127.0.0.1:11433` |
| TTS request timeout | 300 s (5 min) |
| TTS default profile | `default` (Vivian zh / Ryan en, custom_voice) |
| TTS alt profile | `voice-clone` (Ann zh / Scott en, cloned from ref audio) |
| Audio cadence | 0.8x zh → en → 0.9x zh → en → 1.0x zh → en |
| Segment pause | 300 ms |
| Audio bitrate | 64 kbps MP3 |
| Dictionary path | `dict/custom_dict.json` |
| Skip simplification | `False` |
| Skip audio | `False` |