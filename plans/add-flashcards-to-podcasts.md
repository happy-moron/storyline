# Add Flashcard Generation to the Podcast Pipeline

## Current State

The flashcard pipeline (`flashcard/run_pipeline.py`) runs as a standalone CLI. The podcast pipeline
(`podcast/run_pipeline.py`) has three stages:

```
Stage 1: generate_podcast()    → LLM loaded → script + validation/fix → LLM stopped
Stage 2: create_ereader()      → TTS loaded  → HTML + sentence audio  → TTS stopped
Stage 3: create_mp3()          → TTS loaded  → full mp3 rendering     → TTS stopped
```

Each stage starts/stops services independently. The LLM is already running after Stage 1 finishes
(good: we can piggyback flashcard vocab extraction). TTS and ComfyUI both need the GPU so they
cannot overlap (good: we can insert image gen between Stage 2 TTS and Stage 3 TTS).

## Goal

Make flashcard generation a default-on option in the main podcast pipeline:

- Vocab extraction (LLM prompt) runs while the LLM is already hot from Stage 1
- Image generation (ComfyUI) runs between e-reader audio and MP3 mixing — after TTS is released
  but before it's needed again
- The `--skip-flashcards` flag disables the entire flashcard stage
- The cleanup script removes orphaned flashcard artifacts from failed episodes

---

## Step 1: Refactor `_extract_entries` to support caller-managed LLM lifecycle

**File:** `src/storyline/flashcard/run_pipeline.py`

**Problem:** `_extract_entries()` internally does `service_manager.start_if_needed("llm")` /
`stop_if_running("llm")`. When called from `generate_podcast()`, the LLM is already running and
should stay running — `generate_podcast` manages its own LLM lifecycle.

**Changes:**

1. Add an `llm_already_running: bool = False` parameter to `_extract_entries()`.
2. Guard the start/stop block:

```python
def _extract_entries(
    ...,
    llm_already_running: bool = False,
) -> list[list[str]]:
    if not force:
        cached = _load_cached_entries(output_dir)
        if cached is not None:
            return cached

    if not llm_already_running:
        service_manager.start_if_needed("llm")
    try:
        entries = extract_vocab(...)
    finally:
        if not llm_already_running:
            service_manager.stop_if_running("llm")

    _save_cache(output_dir, entries)
    return entries
```

3. Update the `run_flashcard_pipeline()` call site to pass `llm_already_running=False` (standalone
   CLI path stays unchanged).

**Verification:** Standalone flashcard CLI still works (`python -m storyline.flashcard.run_pipeline <ep>`).

---

## Step 2: Add flashcard vocab extraction to `generate_podcast()`

**File:** `src/storyline/podcast/create_podcast.py`

**Changes:**

1. Add `flashcards: bool = True` parameter to `generate_podcast()`.
2. Once the script is validated and fixed (`_validate_and_fix` returns successfully), call
   flashcard vocab extraction while the LLM is active:

```python
if flashcards:
    log.info("event=flashcard_vocab_stage")
    t0 = time.time()
    flashcard_output_dir = (
        Path(script_dir).parent.parent / "books" / "podcasts" / selection.theme_slug / "flashcards"
    )
    from storyline.flashcard.run_pipeline import _extract_entries, _write_text_files_and_collect_prompts

    entries = _extract_entries(
        script_path=script_output,
        prompt_template_path="prompts/flashcard-vocab-from-script.md",
        service_manager=service_manager,
        models=config.models,
        output_dir=flashcard_output_dir,
        force=False,
        llm_already_running=True,
    )
    _write_text_files_and_collect_prompts(flashcard_output_dir, entries)
    log.info(
        "event=flashcard_vocab_done theme=%s entries=%d duration_ms=%d",
        selection.theme_slug, len(entries), int((time.time() - t0) * 1000),
    )
```

3. Update the return to include the flashcard output dir so the caller knows the cache exists:

```python
return {"selection": selection, "script_output": script_output,
        "flashcard_dir": flashcard_output_dir if flashcards else None}
```

**Verification:** Run `generate_podcast` and confirm `flashcard_entries.json` + `text_*.txt` +
`image_prompts.json` appear in `books/podcasts/<slug>/flashcards/`.

---

## Step 3: Add flashcard image gen + PDF to `run_full_pipeline()`

**File:** `src/storyline/podcast/run_pipeline.py`

**Changes:**

1. Add `--skip-flashcards` CLI argument (default: `False`, meaning flashcards are on).
2. Pass `flashcards=not args.skip_flashcards` to `generate_podcast()`.
3. Ensure TTS is fully stopped after Stage 2 before starting image gen. The current code doesn't
   explicitly stop TTS between stages — add `service_manager.stop("tts")` after `create_ereader()`
   returns.
4. Insert Stage 2.5 between e-reader and MP3:

```python
# ── Stage 2.5 — Flashcards (image gen + PDF) ────────────────────────
if not skip_flashcards:
    log.info("event=pipeline_stage stage=2.5 flashcards_image")
    t0 = time.time()
    flashcard_dir = result["flashcard_dir"]
    if flashcard_dir and (flashcard_dir / "flashcard_entries.json").is_file():
        _run_flashcard_image_stage(flashcard_dir, service_manager)
        log.info(
            "event=pipeline_stage_complete stage=2.5 duration_ms=%d",
            int((time.time() - t0) * 1000),
        )
    else:
        log.warning("event=flashcard_skip_no_entries dir=%s", flashcard_dir)
```

5. Extract a `_run_flashcard_image_stage(output_dir, service_manager)` helper from
   `flashcard/run_pipeline.py`'s Stage 3-4 logic (the ComfyUI + PDF part, NOT the LLM extraction).
   This avoids duplicating code. The original `run_flashcard_pipeline()` can call this too.

**Refactor in `flashcard/run_pipeline.py`:**

Add a standalone function that can be called from both the CLI and the podcast pipeline:

```python
def generate_flashcard_images_and_pdf(
    output_dir: Path,
    service_manager: ServiceManager,
) -> Path:
    # Read cached prompts
    prompts_path = output_dir / IMPROMPTS_FILE
    with open(prompts_path, encoding="utf-8") as f:
        image_prompts = json.load(f)

    # Stage 3: Generate images via ComfyUI
    from storyline.flashcard.image_gen import generate_images
    generate_images(prompts=image_prompts, output_dir=output_dir,
                    service_manager=service_manager)

    # Stage 4: Generate PDF
    from storyline.flashcard.pdf_gen import generate_pdf
    pdf_path = generate_pdf(input_dir=output_dir)
    return pdf_path
```

Then `run_flashcard_pipeline()` calls both `_extract_entries` + `_write_text_files_and_collect_prompts`
followed by `generate_flashcard_images_and_pdf()`.

**Verification:** Run `python -m storyline.podcast.run_pipeline` and confirm a full pipeline run
with flashcards working. Confirm `--skip-flashcards` skips the stage.

---

## Step 4: Update `scripts/podcast_cleanup.py`

**File:** `scripts/podcast_cleanup.py`

**Changes:**

In `_cleanup_artifacts()`, add flashcard cleanup:

```python
flashcards_dir = books_dir / slug / "flashcards"
if flashcards_dir.is_dir():
    n_files = sum(1 for _ in flashcards_dir.rglob("*") if _.is_file())
    if not dry_run:
        shutil.rmtree(flashcards_dir)
    removed.append(f"{flashcards_dir}/ ({n_files} files)")
```

**Verification:** Create a partial episode (kill pipeline mid-flashcard-gen or mid-MP3), run
`python scripts/podcast_cleanup.py --dry-run`, confirm flashcards dir shows up. Run without
`--dry-run`, confirm it's removed.

---

## Step 5: Integration test

1. **Happy path:** Run a full pipeline from scratch — script → e-reader → flashcards → MP3.
   Verify all artifacts exist:
   - `books/podcasts/<slug>/flashcards/flashcard_entries.json`
   - `books/podcasts/<slug>/flashcards/image_prompts.json`
   - `books/podcasts/<slug>/flashcards/text_1.txt` through `text_6.txt`
   - `books/podcasts/<slug>/flashcards/img_1.png` through `img_6.png`
   - `books/podcasts/<slug>/flashcards/cards.pdf`
   - `~/temp/audio/podcasts/<slug>.mp3`

2. **Resume after image gen failure:** Delete `img_*.png` and `cards.pdf`. Re-run pipeline.
   Vocab extraction should skip (cache hit), image gen should re-run, PDF should regenerate.

3. **`--skip-flashcards`:** Run with flag. Confirm no flashcard dir is created, pipeline
   completes normally.

4. **Cleanup:** Kill pipeline mid-run. `podcast_cleanup.py --dry-run` should list flashcard dir
   as orphaned. Real run should remove it.

---

## Files Summary

| File | What changes |
|---|---|
| `src/storyline/flashcard/run_pipeline.py` | Add `llm_already_running` param to `_extract_entries`; extract `generate_flashcard_images_and_pdf()` helper |
| `src/storyline/podcast/create_podcast.py` | Add `flashcards` param; call `_extract_entries` + `_write_text_files_and_collect_prompts` after script fix |
| `src/storyline/podcast/run_pipeline.py` | Add `--skip-flashcards` flag; insert Stage 2.5; ensure TTS released between stages |
| `scripts/podcast_cleanup.py` | Add `flashcards/` directory to orphan cleanup |