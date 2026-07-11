# Book Creation Pipeline Walkthrough

## Entry Point

`src/learnbook/book/create_book.py` — invoked via CLI or imported programmatically. The `create_book()` function orchestrates the full pipeline; the `__main__` block parses CLI args, loads model config, and calls `create_book()`.

### CLI Arguments

| Flag | Default | Description |
|---|---|---|
| `-i` / `--input` | *(required)* | Plaintext book file path |
| `-a` / `--author` | *(required)* | Author name (used in output path) |
| `-m` / `--models` | `""` | Comma-separated model list (overridden by config file) |
| `--max` | `500` | Max number of chunks to process |
| `--skip-simplify` | `False` | Skip the simplification step |
| `--skip-audio` | `False` | Skip audio generation |

---

## Pipeline Steps

The pipeline processes a plaintext book through six stages. For a book by `{author}` named `{book}`, all output lands under `books/{author}/{book}/`.

```
books/{author}/{book}/
├── split/
│   ├── source/        # Step 1: chunked raw text
│   └── simple/        # Step 2: simplified English
├── json/
│   ├── source/        # Step 3: translation JSON
│   └── tokenized/     # Step 4: POS-annotated JSON
└── audio/             # Step 5: per-sentence MP3 files
```

Dictionary updates go to `dict/custom_dict.json` (Step 6).

---

### Step 1 — Text Splitting

**Module:** `src/learnbook/book/split_text.py`

The input plaintext book is split on paragraph boundaries (`\n\n+`). Paragraphs are accumulated into chunks of ≤ 2000 characters. Each chunk is written as `{book}_{N}.txt` under `split/source/`, where `N` is a sequential number starting at 1.

Files are processed in natural numeric order (e.g. `1, 2, 10, 11`, not `1, 10, 11, 2`).

---

### Step 2 — Simplify English Text

**Module:** `src/learnbook/prompt_utils/run_prompt.py`
**Prompt:** `prompts/simplify-text.txt`
**Default model:** `qwen35-9b`

Each chunk is sent to the LLM to simplify the English. The prompt instructs:
- Target ~9th grade reading level
- Reduce complex grammar, replace rare words with common ones
- Preserve proper nouns and technical terms
- Output one or two related sentences per line
- No markdown, no explanations

Output: `split/simple/{N}.txt`

If `--skip-simplify` is set, the raw chunk is copied directly to this location.

**How LLM calls work:** `run_prompt()` uses `zsp_llm_client.PromptRunner`, which reads the prompt template, appends the input file content, calls the OpenAI-compatible endpoint at `http://127.0.0.1:11432/v1`, strips any `think` / reasoning tags from the response, and writes it to the output file.

---

### Step 3 — Translate to Chinese (with Backtranslation)

**Module:** `src/learnbook/prompt_utils/run_prompt.py`
**Prompt:** `prompts/translate_deepseek.txt`
**Default model:** `qwen35-9b`

Each simplified English chunk is sent to the LLM to translate into simplified Chinese at HSK1-2 level. The LLM is also asked to produce an English backtranslation of the Chinese result.

The prompt instructs:
- Focus on core meaning, not literal translation
- Use HSK 1-2 vocabulary and grammar
- Do not transliterate proper names
- Output JSON array of objects: `{"chinese": "...", "english": "..."}`

Output: `json/source/{N}.json` — a JSON array of sentence pairs.

---

### Step 4 — Tokenize & POS-Annotate Chinese

**Module:** `src/learnbook/prompt_utils/run_prompt.py` then `src/learnbook/book/fix_missing_commas.py`
**Prompt:** `prompts/tokenize.txt`
**Default model:** `qwen35-35b-a3b` (largest model; this is the most complex task)

The translation JSON is sent to the LLM for token-level annotation. Each Chinese sentence is broken into tokens, each token annotated with:
- `[Text, Pinyin, POS_Tag]` — where POS tag uses a 26-tag system (e.g. `n` noun, `v` verb, `r` pronoun, etc.)
- Compound words may include nested component breakdowns

After the LLM returns, `fix_missing_commas.py` runs to repair a common JSON syntax issue: LLMs sometimes omit commas between adjacent array elements in the token list. The fixer uses a regex to detect `][` boundaries and insert the missing comma. A backup (`.bak`) is saved before overwriting.

Output: `json/tokenized/{N}.json`

---

### Step 5 — Audio Generation

**Module:** `src/learnbook/audio/audiobook_gen_qwen3.py` + `audiobook_gen_base.py`
**TTS endpoint:** `http://127.0.0.1:11433` (Qwen3-TTS Flask service)

The source translation JSON (`json/source/`) is processed for audio. Each sentence pair (`chinese`, `english`) is rendered with a specific audio cadence (repetition sequence):

```
slow Chinese (0.8x)  →  English  →  medium Chinese (0.9x)  →  English  →  normal Chinese  →  English
```

Each segment is separated by a 300 ms pause; sentences are separated by 800 ms.

**Outputs:**
- Combined audiobook MP3: `/home/zspdude/temp/audio/{book}/{N}.mp3`
- Per-sentence standalone MP3s: `books/{author}/{book}/audio/{N}_{sentenceNum}.mp3` (Chinese only, normal speed, 64 kbps)

If `--skip-audio` is set, this step is skipped entirely.

**Audio config** is in `src/learnbook/config/audio.toml`. Voices are defined with a `mode` field:

- **`custom_voice`** — calls `/custom_voice` with a Qwen3 pretrained speaker name (e.g. Vivian, Ryan) and an optional `instruct` string for voice direction.
- **`voice_clone`** — calls `/voice_clone` with a reference audio file + its transcript to clone a custom voice.

Profiles select which voices to use and the pause timing. The default profile is `custom-voice` (Vivian for Chinese, Ryan for English). A `voice-clone` profile (Ann/Scott via cloned reference audio) is also provided. The profile is selected via `profile_key` when calling `process_json_to_audio()`.

---

### Step 6 — Dictionary Building

**Module:** `src/learnbook/book/create_custom_dict.py`
**Prompt:** `prompts/create_single_dictionary_entry.txt`
**Default model:** `qwen35-9b`

The tokenized JSON is scanned for every Chinese word token. Any word that:
- Is not punctuation
- Is not already in `dict/custom_dict.json`
- Has non-empty simplified text

…is sent to the LLM to create a dictionary entry. The LLM returns JSON with:
- The word (simplified Chinese)
- Pinyin
- A list of meanings/senses, each with:
  - A meaning description
  - An example sentence (Chinese, pinyin, English translation)

The dictionary (`dict/custom_dict.json`) is saved after each file is processed.

---

## Service Orchestration

**Module:** `src/learnbook/services/manager.py`

Because VRAM is limited, only one of the LLM or TTS models can run at a time. The `ServiceManager` singleton manages this mutual exclusion:

| Service | systemctl unit | Port | Health check |
|---|---|---|---|
| LLM | `llamacpp` | 11432 | `GET /v1/models` |
| TTS | `qwentts` | 11433 | `GET /health` |
| Image-gen | `qwen-image-gen` | 11434 | `GET /health` |

The pipeline lifecycle:
1. **Start:** LLM is started at the beginning of `create_book()`.
2. **During audio (Step 5):** Before each chunk's audio generation, the TTS service is started (which implicitly stops the LLM). After audio, the TTS is stopped and the LLM is restarted for subsequent steps.
3. **End:** LLM is stopped in a `finally` block.

---

## Model Configuration

**File:** `src/learnbook/config/llms_for_tasks.toml`

Each pipeline step can use a different model. Default values:

```toml
[default]
models = ["qwen35-9b"]

[simplify]
models = ["qwen35-9b"]

[translate]
models = ["qwen35-9b"]

[tokenize]
models = ["qwen35-35b-a3b"]   # larger model for POS tagging

[dictionary]
models = ["qwen35-9b"]
```

Task-specific models override the `[default]` profile. Each entry is a preference-ordered list; the PromptRunner tries models in order until one succeeds.

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

## Quick Reference: Default Settings Summary

| Setting | Default |
|---|---|
| Chunk max size | 2000 characters |
| Max chunks processed | 500 |
| Simplify model | `qwen35-9b` |
| Translate model | `qwen35-9b` |
| Tokenize model | `qwen35-35b-a3b` |
| Dictionary model | `qwen35-9b` |
| LLM endpoint | `http://127.0.0.1:11432/v1` |
| LLM request timeout | 300 s (5 min, enforced by `concurrent.futures` in `run_prompt.py`) |
| TTS endpoint | `http://127.0.0.1:11433` |
| TTS default profile | `default` (Vivian zh / Ryan en via pretrained speakers) |
| TTS alt profile | `voice-clone` (Ann zh / Scott en, cloned from ref audio) |
| Audio speed cadence | 0.8x → 1.0x → 0.9x → 1.0x → 1.0x → 1.0x |
| Segment pause | 300 ms |
| Sentence pause | 800 ms |
| Audio bitrate | 64 kbps MP3 |
| Dictionary path | `dict/custom_dict.json` |
| Skip simplification | `False` |
| Skip audio | `False` |
