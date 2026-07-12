# Plan Summary (DO NOT MODIFY)

This plan implements "chunk-based" audio processing.
The current state (prior to plan implementation) of the create_book pipeline does sentence by sentence audio processing. Texts are split into blocks but the audio is rendered sentence by sentence to support the HTML/JS/CSS reader that plays one sentence at a time.

This has the limitation that isolated sentences don't sound nice thorough TTS because they lack the surrounding context (scene, etc.) which is implicit for "natural" reading

The goal of this plan is to move to processing text in natural reading blocks of text or "chunks" which have enough context for the TTS model to produce natural sounding speech.

This means that audio files will change from per-sentence audio files to longer audio files, which will then be run through a new ForcedAligner model to identify word timestamps. 

The text processing is based on having a sentence per each line. Sometimes the models mess up and put a couple short sentences on the same line; this doesn't really matter. By tracking the number of words per line in the source texts while assembling the reading chunks, the line boundaries can be clearly marked thoroughout the entire process.

So after running forced alignment, the pipeline will need to calculate the start/end line timestamps. The api supports start/end timestamp information, so processing probably wants to calculate "middle of the pause" based on the start of next word (if it exists).

The longer stitched audio file construction (e.g. with readers alternating) will need to rely on the known line wordcounts/boundaries to slice/cut audio sections to assemble the needed audio.

The HTML/JS/CSS reader needs to be updated to work on the new model. Language should be made consistent across the pipeline/reader:
* "Chapters" for the higher-level split blocks
* "Chunks" for the natural-reading chunks
* "Line" for each reading line

The reader app should still support playing audio per-line but it will need to use known line-wordcounts and word timestamps to play correct audio sections within a chunk.

# Background / Reading

The docs for the ForcedAligner endpoint are in
external_docs/qwen3-tts-flask/README.md

# Implementation Order / Plan

## 1 - Set up a RealWorld test harness to make sure the pipeline works

There should be a RealWorld test harness that is set up which can run the entire pipeline - text processing, audio generation (including both sentence based generation and the repeating/joined together longer audio. )

* run a short single chapter (multi-block) book text through the entire pipeline
* Leave the artifacts existing for manual examination after the run is finished, but clean expected locations before the test run so that it's re-runnable
* configured to use a local llm for the text processing stages (default 'qwen36-35b-a3b-nothink')
* generate sentence level audio and joined together audio


### Key files impacted

| File | Role |
|------|------|
| `test_pipeline_manual.py` | Existing manual test script testing individual pipeline steps |
| `tests/integration/test_pipeline.py` | Existing pytest integration tests (markers: `integration`, `slow`) |
| `tests/conftest.py` | Pytest configuration (custom markers, fixtures) |
| `src/storyline/book/create_book.py` | Pipeline entry point — the harness exercises this |
| `src/storyline/services/manager.py` | ServiceManager — starts/stops LLM and TTS for the test run |
| `src/storyline/config/pipeline_config.py` | PipelineConfig — profile selection, path resolution |
| `src/storyline/config/pipeline.toml` | Pipeline defaults + `[profiles.test]` (max_chunks=1) |
| `src/storyline/config/services.toml` | Service timeouts, ports, health endpoints |
| `src/storyline/config/audio.toml` | Audio profiles and voice definitions |

### Known implementation details

- Existing integration tests use `pytest` with custom markers: `--run-integration --run-slow`.
- A manual test script (`test_pipeline_manual.py`) already exercises split → translate → tokenize → dictionary against `books_src/childrens/gossie.txt`.
- PipelineConfig has a `test` profile (`max_chunks=1`, `skip_simplify=true`, `skip_audio=false`).
- ServiceManager handles LLM ↔ TTS mutual exclusion via systemctl and GPU memory polling.
- Audio is currently generated as per-sentence standalone MP3s + combined audiobook MP3.
- The harness described in the plan should be a separate RealWorld suite (distinct from existing integration tests per AGENTS.md guidelines on RealWorld tests).

### Open questions

1. The harness should test the **current** sentence-based pipeline first (as a baseline), then be adapted after Steps 2–4 to continue to provide proof that the implementation works as changes are made
2. "Chapters" are defined as the blocks which the text-splitting script provides (~2000 chars)
3. Test artifacts can live in a dedicated test-named book on the regular processing paths


## 2 - Replace the 'translate_and_simplify' flow

The existing flow should be replaced with a separate, skippable 'simplify' prompt which runs to simplify a chapter of text to a targeted reading level and to format it into reading lines.

A prompt has been written in prompts/simplify.md

The earlier translate_and_simplify flow was a failed experiment at joining the two stages.

Some handwritten source texts will already be suitably formatted and at an appropriate reading level; their chapters won't need to run through this stage.

### Key files impacted

| File | Role |
|------|------|
| `src/storyline/book/create_book.py` | Translate step logic — controls which prompt is used |
| `prompts/translate_and_simplify.md` | Current combined prompt — to be replaced |
| `prompts/translate.md` | Current Chinese-only translation prompt — keep/modify? |
| `src/storyline/config/pipeline_config.py` | `skip_simplify` flag, `prompts` dict entries |
| `src/storyline/config/pipeline.toml` | `paths.prompts.translate`, `paths.prompts.translate_and_simplify`, `pipeline.skip_simplify` |

### Known implementation details

- Two translation paths exist, gated by `config.skip_simplify`:
  - `skip_simplify=true` → uses `translate.md` (Chinese-only per line; pipeline appends `|||` to each line for empty English)
  - `skip_simplify=false` → uses `translate_and_simplify.md` (Chinese `|||` backtranslation per line)
- `translate_and_simplify.md` was described as "a failed experiment" — the plan wants to split simplification into its own stage.
- All existing processed books used `skip_simplify=true` (raw English → Chinese translation, no simplification).
- `PipelineConfig` exposes `skip_simplify` as a CLI boolean flag (`--skip-simplify` / `--no-skip-simplify`).
- The prompts dict in pipeline.toml will need a new entry (e.g. `paths.prompts.simplify`) and likely removal of `paths.prompts.translate_and_simplify`.
- The simplify should be skipped by a cli / config option which should default to false
- The preformatted source will be English langauage text coming out of splitting the plaintext english source books.
- Translate.md doesn't change
- the simplify step should produce a new intermediate artifact file (e.g. `split/simple/{N}.txt` as OVERVIEW.md originally described
- Translation is expected to preserve the line count exactly (one Chinese line per English line)

## 3 - Add a chunking step to the pipeline


The pipeline should add a stage to break up the text into natural-reading chunks. The output of this will not change the content of the reading lines but will use a simple format to mark reading sections and their reading instructions. 

* wire up the prompt ( a prompt is implemented in prompts/tts_chunking_prompt_single_narrator.md)
* Design parser for the output format described in the sample prompt
    * rather than repeat full chapter, possibly just output previous/next sentences for tag insertion points. 
* Design output processing
    * Should validate that insertion points exist and are valid
    * extract out reading instructions and break the chunking source text into chunks (maybe just mapping reading instructions to start indices)


### Key files impacted

| File | Role |
|------|------|
| `prompts/tts_chunking_prompt_single_narrator.md` | **Already exists** — chunking prompt with `@instruct:` output format |
| `src/storyline/book/create_book.py` | New pipeline stage between translate and audio |
| `src/storyline/prompt_utils/run_prompt.py` | LLM invocation (no changes expected) |
| `src/storyline/book/parse_pipe_format.py` | Existing pipe format parsers — chunk format likely needs its own parser module |
| `src/storyline/config/pipeline_config.py` | New prompt entry in prompts dict |
| `src/storyline/config/pipeline.toml` | `paths.prompts.chunk` (new entry) |

### Known implementation details

- A chunking prompt already exists at `prompts/tts_chunking_prompt_single_narrator.md`. 
- Chunking happens after simplification and before translation in the pipeline.- Word counts per line can be computed and embedded on-the-fly by the audio stage from the chunk text
- There should probably be a dedicated chunk parser module (e.g. `parse_chunk_format.py`) 

## 4 - Update audio generation to use chunks


* pass text in chunks (not reading lines) to tts audio gen 
    * If aggregate/joined audio is being generated, English chunks (simplified source) should be passed to the default English voice
* Pass reading instructions as 'instruct' for customvoice (both English and Chinese)
* Pass resulting audio through forcedaligner to generate word timestamps
* Calculate reading line timestamps based on word timestamps
    * "split in the middle of the gap" between end of last word and start of next sentence.
* Design the output file format for chunk-based audio for the reader app to consume
* The aggregate/joined audio should use reading-line timestamps to grab audio segements/slices for joining


### Key files impacted

| File | Role |
|------|------|
| `src/storyline/audio/audiobook_gen_qwen3.py` | `Qwen3TTSService` client — needs `forced_align()` method; `generate_tts_audio()` dispatcher |
| `src/storyline/audio/audiobook_gen_base.py` | `process_sentence()`, `process_json_to_audio_common()`, `load_profile_from_toml()`, `change_tempo()` — core audio assembly logic to be rewritten |
| `src/storyline/config/audio.toml` | Voice definitions, audio profiles — may need chunk-specific profiles |
| `src/storyline/book/create_book.py` | Audio step orchestration — currently calls `process_json_to_audio()` |
| `src/storyline/services/manager.py` | Service lifecycle — TTS + aligner orchestration (aligner shares TTS process, separate VRAM slot) |
| `src/storyline/config/services.toml` | `services.tts.request_timeout` (60s) — may need adjustment for long chunk texts |

### Known implementation details

- **TTS client** (`Qwen3TTSService`):
  - `generate_audio(text, language, speaker, instruct="")` → `/custom_voice`
  - `generate_voice_clone(text, language, ref_audio_path, ref_text)` → `/voice_clone`
  - Both return `pydub.AudioSegment` decoded from base64 WAV
  - No client method exists yet for `/forced_aligner` — needs to be added
- **Forced aligner** (`POST /forced_aligner`):
  - Parameters: `audio` (base64 WAV), `text` (reference text), `language`
  - Response: `{"words": [{"text": "...", "start_time": N, "end_time": N}, ...]}`
  - The aligner model has its own VRAM slot (~1.7 GiB), **separate** from TTS models (~3.4 GiB) — does NOT require stopping TTS to use
  - Both slots are managed by the same Flask server process
- **Current audio profiles** (`audio.toml`): define a zh/en repetition cadence per sentence (3 zh + 3 en at different speeds with 300ms pauses). Supported modes: `custom_voice` (Qwen3 pretrained speaker + optional instruct) and `voice_clone` (reference audio).
- **`change_tempo()`** uses the external `soundstretch` CLI for pitch-preserving speed change.
- **Current outputs**: standalone per-sentence MP3s → `books/{author}/{book}/audio/{prefix}_{chapter}_{sentenceNum}.mp3`; combined audiobook → `audiobook_dir/{book}/{prefix}_{N}.mp3`
- **TTS request timeout** is 60s (services.toml). A long chunk with many words could exceed this.
- The text chunks to supply are known by the groups of sentences identified by the 'instruct' tags.
- "split in the middle of the gap between end of last word and start of next sentence."
   - Basically `(word_n.end_time + word_{n+1}.start_time) / 2`
   - If the first line, it's just start = 0.0; last line end is = audio duration.
   - Edge case: If a chunk has only one line duration is the full audio
- **Output metadata format for the reader:** This is a critical design point — the contract between pipeline and reader. Needs a concrete spec. Suggested structure:
   ```json
   {
     "chunks": [
       {
         "audio": "chunk_01.mp3",
         "instruct": "Frightened whisper",
         "lines": [
           { "start": 0.0, "end": 2.34, "word_count": 5 },
           { "start": 2.56, "end": 5.10, "word_count": 7 }
         ],
         "words": [
           { "text": "你", "start_time": 0.12, "end_time": 0.34 },
           ...
         ]
       }
     ]
   }
   ```
- `books/{author}/{book}/chunks/{prefix}_{N}.json` is a good path for the file

- Long chunk texts may exceed the 60s TTS request timeout. The timeout should be increased to reflect real-world timing in services.toml

- **Joined/aggregate audio:** The plan mentions "the aggregate/joined audio should use reading-line timestamps to grab audio segments/slices for joining. This is the Chinese and English segments according the pattern in audio.toml
- Chunk profiles just define voice + language (no speed variation within a chunk). Instruct tags are passed per-chunk as the `instruct` parameter


## 5 - Update the reader to use reading line timestamps for audio playback

* Update the reader to honor the chapter/chunk/reading line model
* implement reading-line playback based of timestamped section of audio according to reading line timestamp metadata


### Key files impacted

| File | Role |
|------|------|
| `index.html` | ~400 lines of JS — the entire reader implementation. `loadChapter()`, `loadContent()`, audio playback, next/prev navigation, speed control. |
| `styles.css` | New styles for line highlighting during playback, chunk indicators |
| `books/manifest.json` | May need new fields (chunk metadata paths, format version) |

### Known implementation details

- **Current data loading:** `loadChapter()` fetches two pipe files:
  - `pipe/tokenized/{prefix}_{N}.txt` → parsed by `parseTokenizedPipe()` → token data for display
  - `pipe/source/{prefix}_{N}.txt` → parsed by `parseSourcePipe()` → English translations
- **Current DOM structure:** Each sentence gets a `.sentence` div containing `.pinyin`, `.chinese`, `.translation`, and a hidden `<audio>` element with `id="sentence-audio-{i}"`.
- **Current audio model:** Per-sentence MP3s at `books/{author}/{book}/audio/{prefix}_{chapter}_{sentenceNum}.mp3`.
- **Next/prev navigation:** Parses the current audio element's ID to get the sentence index, then finds the adjacent audio element by ID and plays it. `audio.addEventListener('ended', () => nextBtn.click())` auto-advances.
- **Speed control:** Iterates all `#content audio` elements and sets `playbackRate`. Resets to 1.0x on new chapter load.
- **Double-click token:** Plays the enclosing sentence's audio from the beginning.
- **English visibility:** Toggled via CSS class, persisted in localStorage.
- **No line highlighting** exists during playback.
- **Manifest format:** `{ books: [{ author, books: [{ slug, title, prefix }] }] }`. `prefix` is used for file path construction.


## 6 - Update the project documentation

### Key files impacted

| File | Role |
|------|------|
| `OVERVIEW.md` | Existing detailed pipeline walkthrough (outdated — references JSON format, old simplify step, old model names) |
| `OVERVIEW-benchmark.md` | Benchmark pipeline documentation |
| `README.md` (new) | Getting-started instructions per the plan |
| `AGENTS.md` | Project-level guidelines — may need terminology updates |

### Known implementation details

- `OVERVIEW.md` is thorough (~250 lines) but references outdated pipeline stages:
  - Step 2 "Simplify English Text" as a separate stage (removed before this plan)
  - JSON format for translation and tokenization (now pipe format)
  - Model names `qwen35-9b`, `qwen35-35b-a3b` (may be outdated vs current `qwen36-35b-a3b-nothink`)
- `OVERVIEW.md` describes the audio cadence and service orchestration in detail — these sections will need significant updates.
- The plan says "Create a README.md" — having both a README.md and OVERVIEW.md could cause maintenance divergence. That's fine. They serve different purposes