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

## Configuration

All TOML config files live in `src/storyline/config/`:

- `pipeline.toml` — paths, pipeline behaviour, named profiles
- `audio.toml` — voice definitions, audio profiles, repetition cadences
- `llms_for_tasks.toml` — LLM provider and per-task model profiles
- `services.toml` — service ports, timeouts, health check endpoints

CLI flags override config values. Named profiles (`--profile quick`, `--profile test`) apply preset overrides.