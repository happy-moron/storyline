# Storyline — Language Learning Materials Generator

Storyline generates graded Chinese reading materials from English books. It runs a pipeline that simplifies, chunks, translates, tokenizes, and produces audio for an interactive web reader.

## What It Produces

- **An HTML/JS/CSS web reader** — displays tokenized Chinese with pinyin, English translations, a popup dictionary, and per-line audio playback
- **Audio** — TTS-generated Chinese audio with forced-alignment word timestamps, plus aggregate audiobooks following a spaced-repetition cadence
- **Dictionary entries** — LLM-generated Chinese→English definitions with example sentences

## Prerequisites

- **Python 3.11+** (uses `tomllib`)
- **Linux** with `systemctl --user` for service management
- **GPU workstation** with enough VRAM to run one model at a time (~8 GB minimum)
- **External services** (must be running or startable via systemctl):
  - LLM server at `http://127.0.0.1:11432/v1` (OpenAI-compatible, e.g. llama.cpp)
  - TTS server at `http://127.0.0.1:11433` (Qwen3-TTS Flask service with forced aligner)
- **`soundstretch`** CLI tool — for tempo-preserving audio speed changes
- **`pydub`** — audio manipulation library (requires `ffmpeg` or `libav`)

## Setup

```bash
# Clone the repository
git clone <repo-url> storyline
cd storyline

# Create and activate a virtual environment
python3 -m venv .venv
. .venv/bin/activate

# Install dependencies
pip install requests pydub zsp-llm-client pytest
```

Make sure the external services are installed and configured. See their READMEs:
- LLM client library: `external_docs/zsp-llm-client/README.md`
- TTS server: `external_docs/qwen3-tts-flask/README.md`

## Quick Start

### 1. Start the LLM service

```bash
systemctl --user start llamacpp
```

### 2. Place a plaintext book in `books_src/`

Books are organized by author directory:
```
books_src/
└── author_name/
    └── book-slug.txt
```

The filename (without `.txt`) becomes the book's slug used in output paths.

### 3. Run the pipeline

```bash
python -m storyline.book.create_book \
  -a author_name \
  -i books_src/author_name/book-slug.txt
```

Additional options:
```bash
# Skip simplification (for pre-leveled texts)
python -m storyline.book.create_book -a author_name -i books_src/author_name/book.txt --skip-simplify

# Use cloned voices instead of pretrained speakers
python -m storyline.book.create_book -a author_name -i books_src/author_name/book.txt --audio-profile voice-clone

# Process only 3 chapters with minimal retries (quick run)
python -m storyline.book.create_book -a author_name -i books_src/author_name/book.txt --profile quick
```

### 4. Serve the reader

Start a local HTTP server:
```bash
python -m http.server 8000
```

Open `http://localhost:8000` in a browser. Select an author, book, and chapter to begin reading.

### 5. Stop services when done

```bash
systemctl --user stop llamacpp
systemctl --user stop qwentts
```

## Pipeline Overview

The pipeline processes a book through seven stages:

| Step | Description | Output |
|---|---|---|
| 1. Split | Break book into ~2000-char chapters | `split/source/{N}.txt` |
| 2. Simplify | Simplify English to ~9th grade level (optional) | `split/simple/{N}.txt` |
| 3. Chunk | Group lines into natural-reading chunks with voice directions | `chunks/{N}.json` |
| 4. Translate | Translate to Chinese, one line per sentence | `pipe/source/{N}.txt` |
| 5. Tokenize | POS-annotate Chinese tokens with pinyin | `pipe/tokenized/{N}.txt` |
| 6. Audio | Generate chunk-level TTS, forced alignment, line timestamps, aggregate audiobook | `chunks/{N}.json` (updated) + MP3s |
| 7. Dictionary | Build popup dictionary entries for unknown words | `dict/custom_dict.json` |

Full details in [OVERVIEW.md](OVERVIEW.md).

## Project Layout

```
storyline/
├── src/storyline/
│   ├── book/            # Pipeline orchestration, parsers, splitting
│   ├── audio/           # TTS client, chunk audio generation, audiobook assembly
│   ├── prompt_utils/    # LLM prompt runner
│   ├── services/        # ServiceManager (systemctl lifecycle)
│   ├── config/          # TOML config files (pipeline, audio, services, LLMs)
│   └── benchmark/       # LLM prompt evaluation suite
├── prompts/             # LLM prompt templates
├── books/               # Generated output (manifest, author/book dirs)
├── books_src/           # Source plaintext books
├── dict/                # Custom dictionary
├── tests/               # Unit and integration tests
├── eval/                # Benchmark evaluation data
├── external_docs/       # Docs for external services used
├── plans/               # Design & implementation plans
└── index.html           # Web reader (HTML/JS/CSS)
```

## Testing

```bash
# Unit tests
pytest

# Integration tests (requires running services)
pytest --run-integration --run-slow
```

See [AGENTS.md](AGENTS.md) for testing guidelines and terminology.

## Utility Scripts

### `scripts/voice_design.py` — Generate voice-design audio samples

Designs a custom voice from a natural-language instruction and generates a short audio sample using the TTS server's `/voice_design` endpoint.

**Usage:**
```bash
# English (uses ~/temp/audio/clones/reference-30s-en.txt)
python scripts/voice_design.py -i /path/to/instruct.txt

# Chinese (uses ~/temp/audio/clones/reference-zh.txt)
python scripts/voice_design.py -l zh -i /path/to/instruct.txt

# Custom output file
python scripts/voice_design.py -l en -i instruct.txt -o custom_design.wav
```

**Arguments:**

| Flag | Description |
|------|-------------|
| `-l`, `--language` | Language code: `en` or `zh` (default: `en`). Selects the corresponding reference text file. |
| `-i`, `--instruct-file` | **Required.** Path to a file containing the voice-design instruction (e.g. "Male, 17 years old, tenor range..."). |
| `-o`, `--output` | Output WAV path (default: `~/temp/audio/clones/voice_design_output.wav`). |
| `-t`, `--timeout` | HTTP timeout in seconds (default: 300). |

**Service management:**
- Uses the same `ServiceManager` as the main pipeline (`src/storyline/services/manager.py`)
- Checks if the TTS service (`qwentts`) is running; starts it via `systemctl --user` if not
- Only stops the TTS service if *this script* started it — leaves it running if it was already active

**Example instruct file contents:**
```
Male, 17 years old, tenor range, gaining confidence - deeper breath support now, though vowels still tighten when nervous
```

### `scripts/generate_hidream_image.py` — HiDream image generation via ComfyUI

Reads a prompt from a text file, starts the ComfyUI image-gen service if needed, and generates a HiDream image.

**Usage:**
```bash
# Basic: generate an image from a prompt file
python scripts/generate_hidream_image.py --prompt-file my_prompt.txt

# Custom output path and negative prompt
python scripts/generate_hidream_image.py \
    --prompt-file my_prompt.txt \
    --output results/my_image.png \
    --negative-prompt "blurry, low quality"
```

**Arguments:**

| Flag | Description |
|------|-------------|
| `--prompt-file` | **Required.** Path to a text file containing the positive prompt (one line or multiline). |
| `--output` | Output image path (default: `hidream_output.png`). |
| `--negative-prompt` | Negative prompt text (default: `"bad ugly jpeg artifacts"`). |
| `--timeout` | Max seconds to wait for generation (default: 900). |

**Service management:**
- Uses `ServiceManager` to start ComfyUI (`systemctl --user start comfyui`) if it's not already running
- Automatically stops any other active service (LLM/TTS) to free GPU memory before starting ComfyUI
- Does **not** stop ComfyUI when done — leaves it running for subsequent calls

**Workflow:**
- Loads the HiDream Fast workflow from `external_docs/comfyui_image-gen/hidream_fast_api_example.json`
- Injects the prompt into the positive CLIPTextEncode node and queues the job
- Polls `/history/{prompt_id}` until generation completes, then downloads the image

### `scripts/make_flashcards.py` — Generate printable flashcard PDF

Creates a double-sided 6-card PDF (page 1 = images, page 2 = text) from `img_1..6.png` and `text_1..6.txt` in a directory. Uses reportlab with CJK font support.

**Usage:**
```bash
python scripts/make_flashcards.py /path/to/flashcards_dir
python scripts/make_flashcards.py /path/to/flashcards_dir --page-size a4 --binding short-edge
```

## Flashcard Pipeline

Generates vocabulary flashcards with images from a podcast episode script. The pipeline runs four stages:

| Stage | Description | Output |
|---|---|---|
| 1. Vocab Extraction | LLM identifies 6 key vocab words from the script | Parsed entries (word, pinyin, definition, sample sentence, image prompt) |
| 2. Save Text | Writes flashcard text content to text files | `flashcards/text_1.txt` … `text_6.txt` |
| 3. Image Generation | Generates coloring-book-style images via ComfyUI | `flashcards/img_1.png` … `img_6.png` |
| 4. PDF Assembly | Creates a double-sided printable PDF | `flashcards/cards.pdf` |

### Running the Pipeline

```bash
# Generate flashcards for a single podcast episode
python -m storyline.flashcard.run_pipeline grocery-shopping-for-the-week

# With a custom script directory or output path
python -m storyline.flashcard.run_pipeline grocery-shopping-for-the-week \
    --script-dir books_src/podcasts \
    --output-base-dir books/podcasts

# Override the LLM model
python -m storyline.flashcard.run_pipeline grocery-shopping-for-the-week \
    -m gemma4-26b-a4b-nothink
```

**Arguments:**

| Flag | Description |
|---|---|
| `episode` | **Required.** Episode name matching a script file in `books_src/podcasts/` (e.g. `grocery-shopping-for-the-week`). |
| `--script-dir` | Directory containing podcast scripts (default: `books_src/podcasts`). |
| `--output-base-dir` | Base directory for output; flashcards land in `{output_base_dir}/{episode}/flashcards/` (default: `books/podcasts`). |
| `--prompt-template` | Path to the vocab extraction prompt (default: `prompts/flashcard-vocab-from-script.md`). |
| `-m`, `--models` | Comma-separated list of model IDs (overrides config). |

**Service management:**
- Starts the LLM service for vocab extraction (retries up to 3 times on parse errors), then stops it
- Starts ComfyUI for image generation (~15 min timeout per image, no retries), then stops it
- Only one GPU service runs at a time

**Output** (per episode, under `books/podcasts/{episode}/flashcards/`):
- `text_1.txt` … `text_6.txt` — 6-line text records (headword, pinyin, meaning, sentence, sentence pinyin, translation)
- `img_1.png` … `img_6.png` — coloring-book-style line drawings
- `cards.pdf` — print-ready double-sided PDF (2×3 grid)

### Module Structure

```
src/storyline/flashcard/
├── __init__.py          # Exports run_flashcard_pipeline
├── run_pipeline.py      # Orchestrator & CLI entry point
├── vocab_extract.py     # LLM-based vocab extraction with validation & retries
├── image_gen.py         # ComfyUI image generation (adapted from scripts/generate_hidream_image.py)
└── pdf_gen.py           # PDF assembly via reportlab (adapted from scripts/make_flashcards.py)
```

## Configuration

All TOML config files live in `src/storyline/config/`:

- `pipeline.toml` — paths, pipeline behaviour, named profiles
- `audio.toml` — voice definitions, audio profiles, repetition cadences
- `llms_for_tasks.toml` — LLM provider and per-task model profiles
- `services.toml` — service ports, timeouts, health check endpoints

CLI flags override config values. Named profiles (`--profile quick`, `--profile test`) apply preset overrides.