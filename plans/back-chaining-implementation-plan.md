# Back-Chaining Implementation Plan

## Overview

Add back-chaining (逐句后向读) playback to the podcast e-reader so learners can
build up Chinese sentences from the end, repeating progressively longer fragments
to internalize prosody and sound patterns.

Two new data artifacts are produced per episode:

1. **Backchain breakdowns** — progressive sentence fragments (LLM-generated)
2. **Word-timing metadata** — per-word start/end times (Qwen3 Forced Aligner)

These are stored in `chunks/<slug>_1.json` alongside existing audio metadata.

---

## Part 1 — Backchain Generation Module

### File: `src/storyline/podcast/backchain.py`

#### Data model

```python
@dataclass
class BackchainStep:
    text: str          # e.g. "水果。", "想买水果。", "我想买水果。"
    is_final: bool     # True for the last step (full sentence)

@dataclass  
class BackchainResult:
    line_index: int    # index into script.dialogue
    original: str      # the original full sentence
    steps: list[BackchainStep]
```

#### Public function: `generate_backchains(script, slug, base_dir, config)`

1. Extract Chinese dialogue lines from `script.dialogue` (only lines with
   `profile.lang == "zh"` — which is all podcast dialogue lines since they are
   Chinese).
2. Write lines (one per line) to a temp input file.
3. Call `run_prompt_with_metrics()` with `prompts/back-chaining.md` as the prompt
   template and the dialogue lines as input.
4. Parse the LLM output via `parse_backchain_output()`.
5. Validate via `validate_backchain_output()`.
6. If validation fails, run a retry loop (up to `config.llm_retries` times):
   - Feed the bad output + validation errors back to the LLM via a fix prompt
     (a new `prompts/back-chaining-fix.md` prompt that includes the original
     input and the errors).
   - Parse and validate again.
7. Return `list[BackchainResult]`.

#### Private function: `parse_backchain_output(raw_text, original_lines)`

The LLM output format (from `prompts/back-chaining.md`) is:

```
水果。
想买水果。
我想买水果。

东西。
我的东西。
这是我的东西。
中国人。这是我的东西。
...
```

Parse rules:
- Blocks are separated by blank lines (one or more).
- The last line of each block must match the corresponding original line exactly.
- Each block is converted to a `BackchainResult`.

#### Private function: `validate_backchain_output(results)`

For each `BackchainResult`:
1. **Coverage**: Every original line must have exactly one `BackchainResult`.
2. **Final match**: The last step's text must match the original line exactly
   (after stripping surrounding whitespace).
3. **Ends-with chain**: Each step `i` (0-indexed) must be a suffix of step `i+1`
   (i.e. `steps[i+1].endswith(steps[i])`).

Returns `list[str]` of validation error messages (empty = valid).

### File: `prompts/back-chaining-fix.md`

A fix prompt that:
- Includes the original input lines.
- Shows the previous LLM output.
- Lists the validation errors.
- Instructs the LLM to re-output only the corrected backchains.

---

## Part 2 — Word-Timing Metadata via Forced Alignment

### Modify: `src/storyline/podcast/audio_gen.py`

Add a new function `add_word_timings(chunk_json_path, base_dir, service)`:

1. Load the existing `chunk_json_path`.
2. For each chunk that has an `audio_zh` file:
   - Load the MP3 audio from `base_dir/audio/<audio_zh>`.
   - Get the Chinese text from `script.dialogue[chunk.line_range[0]].chinese`.
   - Call `service.forced_align(audio, text, "chinese")` to get word boundaries.
   - Add a `words` field to `chunk.lines[0]` with the alignment data:

```json
{
  "lines": [{
    "start_zh": 0.0,
    "end_zh": 3.28,
    "words": [
      {"text": "这部", "start_time": 0.0, "end_time": 0.42},
      {"text": "手机", "start_time": 0.42, "end_time": 0.85},
      ...
    ]
  }]
}
```

3. Write the updated JSON back.

### Modify: `src/storyline/podcast/create_ereader.py`

After audio generation (step 6), add a new step:

**Step 6a — Forced Alignment (word timings)**:
- If `not config.skip_audio`:
  - Ensure TTS service is running (the server handles the aligner model
    independently — aligner has its own VRAM slot).
  - Call `add_word_timings(chunk_json_path, base_dir, host_service)`.
  - Log timing metrics.

---

## Part 3 — Backchain Generation Integration

### Modify: `src/storyline/podcast/create_ereader.py`

Add a new step after forced alignment:

**Step 6b — Backchain Generation**:
1. If `not config.skip_audio`:
   - Call `generate_backchains(script, slug, base_dir, config)`.
   - Store results in the chunk JSON:

```json
{
  "chunks": [{
    ...
    "lines": [{
      "start_zh": 0.0,
      "end_zh": 3.28,
      "words": [...],
      "backchain": [
        "手机。",
        "这部手机。"
      ]
    }]
  }]
}
```

   The `backchain` field contains the backchain steps (excluding the final full
   sentence since the player already has that available).
   - Write updated JSON back.

### Modify: `src/storyline/config/pipeline_config.py`

Add back-chain prompts to the default prompts dict:

```python
"backchain": "prompts/back-chaining.md",
"backchain_fix": "prompts/back-chaining-fix.md",
```

Add the tasks to the LLM task config list (`_load_llm_config`):
- `"backchain"`, `"backchain_fix"`

---

## Part 4 — UI Changes

### Modify: `index.html`

Add back-chain playback support to the Chinese sentence display.

#### Data loading

During `loadChapter()`, after parsing `chunkMeta`, extract backchain data from
each chunk's `lines[].backchain` field. Build a `backchainMap` parallel to
`lineMap`:

```javascript
let backchainMap = [];  // backchainMap[lineIndex] = { steps: [...], words: [...] }
```

For each line that has backchain data, store the array of text fragments.

#### Per-line back-chain button

In `loadContent()`, add a small button (▶) at the start of each Chinese line:

```html
<div class="chinese">
  <button class="backchain-btn" data-line-index="${i}">▶</button>
  <span class="chinese-tokens">...</span>
</div>
```

The button is hidden (`.backchain-btn { display: none; }`) by default and only
shown when backchain data exists for that line.

#### Back-chain playback engine

New function: `playBackchain(lineIndex)`

```
function playBackchain(lineIndex):
  stopPlayback()
  data = backchainMap[lineIndex]
  if not data: return
  
  speed = playbackSpeeds[currentSpeedIndex]
  steps = [...data.steps, fullSentence]  // append full sentence as final step
  
  for each step in steps:
    // Calculate playback time ranges for this step using word boundaries
    // The step text maps to a suffix of the full sentence
    // Use word boundaries to find start_time (first word of step) and end_time (last word)
    
    play audio chunk from start_time to end_time
    pause between steps
```

Algorithm for calculating step offsets from word timings:

1. Take the step text (a suffix of the full Chinese sentence).
2. Scan the `words[]` array from the end backwards, accumulating characters
   until the accumulated text matches the step text.
3. The `start_time` of the step is the `start_time` of the first word in the
   matched suffix.
4. The `end_time` is the `end_time` of the last word (the end of the sentence).

Example: Full sentence = "这部手机比我之前的更贵！"
- words: `[{text:"这部", start:0.0, end:0.42}, {text:"手机", start:0.42, end:0.85}, ...]`
- Step "手机比我之前的更贵！" → matches from word index 1 → start=0.42, end=sentence_end

Edge cases: If word-boundary data is missing, fall back to proportional
character-based estimation (step_length / full_length * audio_duration).

#### UI states

- Button shows ▶ when idle.
- During playback, button shows ⏸️ (can be clicked to cancel).
- After playback completes, reverts to ▶.
- Button respects the global playback speed from `speedBtn`.
- Clicking the button while a sentence is playing normally stops normal playback
  and starts the back-chain sequence.

#### CSS additions (styles.css)

```css
.backchain-btn {
  background: none;
  border: 1px solid #ccc;
  border-radius: 3px;
  cursor: pointer;
  font-size: 0.7em;
  padding: 0 3px;
  margin-right: 4px;
  vertical-align: middle;
  color: #666;
}
.backchain-btn:hover { background: #e0e0e0; }
.backchain-btn.playing { color: #e74c3c; border-color: #e74c3c; }
```

---

## Part 5 — Back-Filling Script

### File: `scripts/backfill_backchain.py`

A standalone script to add back-chain and word-timing data to existing podcast
episodes.

```bash
python scripts/backfill_backchain.py [--slug <slug>] [--all]
```

#### Logic

1. **Discover episodes**: Walk `books/podcasts/<slug>/` directories. Filter by
   `--slug` or process all if `--all`.
2. For each episode:
   a. Read `chunks/<slug>_1.json`.
   b. Check if backchain data already exists → skip.
   c. Read `pipe/source/<slug>_1.txt` to get Chinese dialogue lines.
   d. Read the original podcast script from `books_src/podcasts/<slug>.txt`
      and parse it for voice profiles.
   e. **Backchain generation**: Call `generate_backchains()` with the dialogue
      lines. Store results in chunk JSON.
   f. **Forced alignment**: For each chunk, load its MP3 audio, call
      `Qwen3TTSService.forced_align()`, and store word timings in chunk JSON.
   g. Write updated chunk JSON.
3. Ensure TTS service is running before starting (start it if needed via
   `systemctl --user start qwentts`).
4. Log progress per episode.

#### Dependencies

- Uses `storyline.podcast.backchain.generate_backchains()` for backchain gen.
- Uses `Qwen3TTSService` for forced alignment.
- Uses `script_parser.parse_script()` to get voice profiles (for reference
  text, though not strictly needed for alignment — only the Chinese text is).

---

## Part 6 — Testing

### Unit tests (`tests/unit/test_backchain.py`)

Test `parse_backchain_output()` and `validate_backchain_output()`:

1. **Happy path**: Valid backchain output with multiple sentences.
2. **Missing sentence**: Some original lines not in output → validation error.
3. **Final mismatch**: Last step doesn't match original → validation error.
4. **Broken chain**: A step is not a suffix of the next → validation error.
5. **Empty output**: No blocks parsed → validation error.
6. **Extra lines**: More blocks than original lines → validation error.
7. **Multi-sentence lines**: Lines with multiple short sentences (the prompt
   already shows examples of this — "我不是中国人。这是我的东西。" produces
   interleaved chains).

### RealWorld test (`tests/realworld/test_backchain.py`)

A single end-to-end test:
1. Creates a minimal podcast script or uses an existing one.
2. Runs `generate_backchains()` against it with the real LLM.
3. Validates the output.
4. (Optionally) runs `add_word_timings()` against a single audio file to verify
   the forced aligner endpoint.

---

## Part 7 — Configuration

### Modify: `src/storyline/config/pipeline.toml`

Add back-chaining control to the `[defaults.pipeline]` section:

```toml
[defaults.pipeline]
# ... existing ...
skip_backchain = false
```

### Modify: `PipelineConfig`

Add `skip_backchain: bool = False` field.
Wire it through `_apply_cli_overrides()` for the `--skip-backchain` CLI flag.

### Modify: `run_pipeline.py` CLI

Add `--skip-backchain` argument to both `run_full_pipeline` and the `__main__`
argparse block.

---

## Implementation Order

1. **`backchain.py`** — data model, parser, validator (pure functions, testable
   without LLM/TTS)
2. **Unit tests** — test parser/validator
3. **`prompts/back-chaining-fix.md`** — fix prompt for retry loop
4. **Config changes** — add prompt paths, skip flag, task config
5. **Integration into `create_ereader.py`** — add backchain gen step + forced
   alignment step
6. **UI changes** — `index.html` + `styles.css`
7. **Back-fill script** — `scripts/backfill_backchain.py`
8. **RealWorld test** — end-to-end validation