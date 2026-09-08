# Podcast Pipeline

Generates a Chinese-learning podcast episode from HSK grammar points and a theme.
Each episode produces three artifacts:

1. A podcast script (`books_src/podcasts/<episode>.txt`)
2. An e-reader entry (the dialogue section, with per-sentence audio) under `books/podcasts/<episode>/`
3. A full MP3 of the expanded podcast under `dist/audio/podcasts/<episode>.mp3`

The pipeline runs in three stages, which can be executed individually or
all at once via a single end-to-end command.

### Full pipeline (recommended)

```bash
python -m storyline.podcast.run_pipeline
```

Flags:
- `--skip-audio` — skip dialogue sentence audio generation
- `--profile <name>` — use a named profile from `pipeline.toml`
- `-m <models>` — override the model list

### Individual stages

## Prerequisites

```bash
. .venv/bin/activate
```

The stages manage their own services (LLM / TTS) via `systemctl --user`, so no
manual service handling is required. Only one model can be loaded at a time.

## Stage 1 — Generate the script

Picks the next unused grammar points + theme, records the episode in the
registry, then writes vocab and script files.

```bash
python -m storyline.podcast.run_pipeline
```

Outputs:
- `books_src/podcasts/vocab/<theme>.txt`
- `books_src/podcasts/<theme>.txt`
- appends a row to `src/storyline/podcast/existing-episodes.csv`

## Stage 2 — Build the e-reader entry

Parses the script, tokenizes the dialogue, builds chunks + dictionary entries,
generates dialogue sentence audio (voice design + voice clone), and updates the
manifest.

Dialogue sentence audio is generated in batched calls grouped by speaker: one
`/voice_clone` batch request per speaker (shared reference mode). This replaces
N individual API calls with S calls (S = number of speakers, typically 2–3).

```bash
python -m storyline.podcast.create_ereader -i books_src/podcasts/<theme>.txt
```

Useful flags:
- `--skip-audio` — text-only run (no TTS, no voice design)
- `--profile <name>` — use a named profile from `pipeline.toml`
- `-m <models>` — override the model list

Outputs (author `podcasts`, title = episode name):
- `books/podcasts/<episode>/pipe/…` (source, tokenized)
- `books/podcasts/<episode>/chunks/<episode>_1.json`
- `books/podcasts/<episode>/audio/*.mp3` (dialogue sentence audio)
- `voices/<episode>_<speaker>.txt` / `.wav` (dialogue character voice samples)
- updates `books/manifest.json`

## Stage 3 — Generate the full podcast MP3

Stitches together the expanded sequence: intro → dialogue ×3 → line-by-line
(teacher Chinese / student English ×3) → breakdown → outro. Reuses the
Stage 2 dialogue sentence audio where it exists; hosts use the pre-existing
`voices/teacher.*` and `voices/student.*` reference audio.

All audio is pre-fetched in batches before assembly:

- **Host audio** — grouped by speaker; one batch call per speaker (teacher/student).
  Defaults to voice-clone mode sharing the host reference audio; use `--builtin-hosts`
  to switch to Qwen3 built-in voices (Serena/Eric) via `/custom_voice` batch.
- **Character fallback audio** — any character lines not already covered by Stage 2
  dialogue files are batch-generated per speaker via `/voice_clone` shared reference.

```bash
python -m storyline.podcast.create_mp3 -i books_src/podcasts/<theme>.txt
```

Useful flags:
- `--builtin-hosts` — use Qwen3 built-in voices (Serena/Eric) for hosts instead of voice clone
- `--profile <name>` — use a named profile from `pipeline.toml`

Outputs:
- `dist/audio/podcasts/<episode>.mp3` (ID3 artist/album = `podcasts`, title = episode name)

Requires Stage 2 to have run first (for dialogue voice samples and sentence audio).

## Example (full run)

```bash
. .venv/bin/activate
python -m storyline.podcast.run_pipeline
python -m storyline.podcast.create_ereader -i books_src/podcasts/talking-about-last-nights-soccer-results.txt
python -m storyline.podcast.create_mp3 -i books_src/podcasts/talking-about-last-nights-soccer-results.txt
```

## Testing

```bash
. .venv/bin/activate
pytest tests/unit/test_podcast_pipeline.py tests/unit/test_script_parser.py tests/unit/test_podcast_ereader.py tests/unit/test_podcast_audio.py tests/unit/test_podcast_mp3.py
pytest tests/integration/test_podcast_audio.py tests/integration/test_podcast_mp3.py --run-integration
```
