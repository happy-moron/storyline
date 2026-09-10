# Back-Chaining Required Reading

Files and code snippets essential for implementing the plan in
`plans/back-chaining-implementation-plan.md`.

---

## 1. The Forced Aligner Endpoint (already exists)

**File**: `src/storyline/audio/audiobook_gen_qwen3.py` — lines 98–130

```python
def forced_align(self, audio: AudioSegment, text: str, language: str) -> list[dict]:
    """Run forced alignment on audio with known reference text.

    Returns a list of word dicts: [{"text": "...", "start_time": N, "end_time": N}, ...]
    """
    buf = io.BytesIO()
    audio.export(buf, format="wav")
    buf.seek(0)
    audio_b64 = base64.b64encode(buf.read()).decode("utf-8")

    response = self.session.post(
        f"{self.base_url}/forced_aligner",
        json={"audio": audio_b64, "text": text, "language": language.capitalize()},
        timeout=self._align_timeout,
    )
    # ...
    data = response.json()
    words = data.get("words", [])
    # Returns list[dict]: [{text, start_time, end_time}, ...]
```

**Key facts**:
- The aligner has its own VRAM slot on the TTS server — it does NOT conflict with TTS generation.
- It's part of `Qwen3TTSService`, so you use the same `host_service` instance from the audio step.
- Returns `[{text, start_time, end_time}]` where times are float seconds.

**Server doc**: `external_docs/qwen3-tts-flask/README.md` — section "Forced Aligner"

---

## 2. The Chunk JSON Structure

**Current format** (`books/podcasts/<slug>/chunks/<slug>_1.json`):

```json
{
  "chunks": [
    {
      "instruct": "",
      "instruct_zh": "",
      "line_range": [0, 0],
      "audio_zh": "000_setting-up-a-new-phone_1_zh.mp3",
      "lines": [
        {
          "start_zh": 0.0,
          "end_zh": 3.28
        }
      ]
    },
    ...
  ]
}
```

**Target format** (after back-chain implementation):

```json
{
  "chunks": [
    {
      "instruct": "",
      "instruct_zh": "",
      "line_range": [0, 0],
      "audio_zh": "000_setting-up-a-new-phone_1_zh.mp3",
      "lines": [
        {
          "start_zh": 0.0,
          "end_zh": 3.28,
          "words": [
            {"text": "这部", "start_time": 0.0, "end_time": 0.42},
            {"text": "手机", "start_time": 0.42, "end_time": 0.85},
            ...
          ],
          "backchain": [
            "更贵！",
            "之前的更贵！",
            "比我之前的更贵！"
          ]
        }
      ]
    },
    ...
  ]
}
```

- `words` comes from the forced aligner
- `backchain` is the LLM-generated progressive fragments (excluding the final full sentence)
- Both are optional — the player should degrade gracefully if either is missing

---

## 3. How the Chunk JSON Gets Written

**File**: `src/storyline/podcast/audio_gen.py` — lines 160–205

The key pattern to replicate:

```python
# Step 1: Load existing chunk JSON
with open(chunk_json_path, encoding="utf-8") as f:
    chunk_data = json.load(f)
chunks = chunk_data["chunks"]

# Step 2: Mutate chunks in-place (add audio filenames, line timings)
for ci, chunk, audio_file in speaker_items:
    if not chunk.get("audio_zh"):
        chunk["audio_zh"] = audio_file
        chunk["lines"] = [{
            "start_zh": 0.0,
            "end_zh": round(len(audio) / 1000.0, 3),
        }]

# Step 3: Write back
with open(chunk_json_path, "w", encoding="utf-8") as f:
    json.dump(chunk_data, f, ensure_ascii=False, indent=2)
```

**Important**: When adding `words` and `backchain` fields, you follow the same pattern — load, mutate chunks in-place, write back. The chunks are already keyed by `line_range[0]` matching `script.dialogue` index.

---

## 4. The `create_ereader.py` Pipeline Structure

**File**: `src/storyline/podcast/create_ereader.py`

The pipeline has numbered steps. New steps should be inserted after the existing ones:

```
Step 1: Parse script          (parse_script)
Step 2: Write pipe/source     (zh|||en format)
Step 3: Tokenize              (LLM call with retry loop)
Step 4: Create chunks         (initial chunk_json_path)
Step 5: Dictionary            (LLM call)
Step 6: Audio generation      (TTS → voice design + voice clone)
Step 6a: [NEW] Forced alignment  (word timings)
Step 6b: [NEW] Backchain gen     (LLM call)
Step 7: Update manifest
```

Each step logs with the pattern:
```python
log.info("event=ereader_step step=<name> ... duration_ms=%d", ...)
```

**Important ordering constraint**: Backchain generation requires the LLM, so it must run *before* the TTS service is stopped at the end of step 6. The planner notes this is fine because the backchain LLM call happens while the TTS server is still up (useful only if future re-architecting puts it closer to a service stop). The forced alignment step uses the TTS service (aligner runs on the same server) so it also must run before the `finally: service_manager.stop_if_running('tts')` block.

---

## 5. The LLM Call Pattern

**File**: `src/storyline/prompt_utils/run_prompt.py`

The standard way to invoke an LLM prompt and get output back:

```python
from storyline.prompt_utils.run_prompt import run_prompt_with_metrics

# Write input to temp file
input_path = tmp_path / "backchain_input.txt"
input_path.write_text(input_text, encoding="utf-8")

# Call
output_path = tmp_path / "backchain_output.txt"
metrics = run_prompt_with_metrics(
    config.resolve_prompt("backchain"),    # prompt template path
    input_path,                             # Path to input file
    output_path,                            # Path where output is written
    models=config.models,                   # model preference list
    timeout=config.llm_timeout_s,           # timeout in seconds
    prompt_label="backchain",               # for logging/metrics
    extra_options=_resolve_extra_options(config, "backchain"),  # thinking budget etc
)

# Read result
result = output_path.read_text(encoding="utf-8").strip()
```

**Response cleaning**: `run_prompt.py` already strips think tags and markdown fences automatically.

The helper `_llm_call()` in `create_ereader.py` wraps this with logging:
```python
def _llm_call(*, prompt_key, input_path, output_path,
              config, task, prompt_label, log, stem, attempt):
    chars_in = len(input_path.read_text(encoding="utf-8").strip())
    metrics = run_prompt_with_metrics(
        config.resolve_prompt(prompt_key),
        input_path, output_path,
        models=config.models, timeout=config.llm_timeout_s,
        prompt_label=prompt_label,
    )
    parts = [f"event=llm_call prompt={prompt_label} chapter={stem} wall_ms={metrics['wall_ms']}"]
    for k in ("prompt_tokens", "eval_tokens", "prompt_tps", "eval_tps",
              "server_total_ms", "cache", "model_hint"):
        if k in metrics:
            parts.append(f"{k}={metrics[k]}")
    log.info(" ".join(parts))
    return metrics
```

**Retry loop pattern** (from tokenization step in `create_ereader.py`, lines 148–255):
```python
MAX_ATTEMPTS = 3
for attempt in range(MAX_ATTEMPTS):
    if attempt == 0:
        _llm_call(prompt_key=..., input_path=..., output_path=..., ...)
    
    result = parse(output_path)
    errors = validate(result)
    
    if not errors:
        break
    
    if attempt >= MAX_ATTEMPTS - 1:
        raise ValueError(f"Failed after {MAX_ATTEMPTS} attempts: {errors}")
    
    # Build fix input and call again
    fix_input = write_errors_to_tempfile(original_input, bad_output, errors)
    _llm_call(prompt_key="fix_prompt", input_path=fix_input, ...)
```

---

## 6. The `_llm_call` Helper and `_ensure_llm`

Both are local helper functions in `create_ereader.py`:

```python
def _resolve_profile(config, task):
    profiles = config.task_profiles
    if not profiles:
        return None
    default = profiles.get("default") or profiles.get("translate")
    return profiles.get(task, default)

def _resolve_extra_options(config, task):
    budget = config.resolve_thinking_budget(task)
    if budget is not None and budget > 0:
        return {"thinking_budget_tokens": budget}
    return None

def _ensure_llm(service_manager, config, task):
    if service_manager and config.llm_provider == "local":
        service_manager.ensure_llm_profile(_resolve_profile(config, task))
```

For back-chain, you'd call `_ensure_llm(service_manager, config, 'backchain')` — but note that during the ereader step, the LLM has already been stopped (for TTS). So you need to restart it:

```python
# Before backchain LLM call:
if service_manager:
    service_manager.stop_if_running('tts')     # free GPU for LLM
    service_manager.start_if_needed('llm')
    _ensure_llm(service_manager, config, 'backchain')
```

---

## 7. Config Wiring

**File**: `src/storyline/config/pipeline_config.py`

### Prompt paths
Default prompts dict (around line 60–75):
```python
prompts: dict[str, str] = field(default_factory=lambda: {
    "warmup": "prompts/warmup.txt",
    # ... existing entries ...
    "podcast_fix_voice_profiles": "prompts/podcast-fix-voice-profiles-script.md",
    "podcast_pinyin_replace": "prompts/podcast-replace-pinyin.md",
    # ADD:
    "backchain": "prompts/back-chaining.md",
    "backchain_fix": "prompts/back-chaining-fix.md",
})
```

The flat→nested mapping in `from_files_and_args()` (around lines 130–140) automatically picks up `prompts.backchain` if `[paths.prompts]` in TOML defines it. No extra code needed.

### LLM task config (in `_load_llm_config`, around lines 175–195)

The `tasks` tuple needs `"backchain"` and `"backchain_fix"` added:
```python
tasks = (
    "translate", "tokenize", "dictionary",
    "podcast_vocab", "podcast_script",
    "podcast_fix_script", "podcast_fix_dialogue",
    "podcast_fix_voice_profiles",
    "podcast_pinyin_replace",
    "backchain", "backchain_fix",   # ADD
)
```

### Skip flag
Add to the dataclass:
```python
skip_backchain: bool = False
```

Wire through `_apply_cli_overrides()` (lines ~200–210) by adding `"skip_backchain"` to the `attrs` tuple.

---

## 8. The Script Data Model

**File**: `src/storyline/podcast/script_parser.py`

Key types used when working with dialogue:

```python
@dataclass(frozen=True)
class DialogueLine:
    speaker_id: int
    chinese: str
    english: str

@dataclass(frozen=True)
class PodcastScript:
    intro: list[HostLine]
    dialogue: list[DialogueLine]     # ← use this for back-chain input
    breakdown: list[BreakdownLine]
    outro: list[HostLine]
    voice_profiles: dict[int, VoiceProfile]
```

To get just the Chinese dialogue lines for back-chain generation:
```python
script = parse_script(script_text)
chinese_lines = [line.chinese for line in script.dialogue]
```

---

## 9. The UI Audio Playback Architecture

**File**: `index.html`

### Key global state
```javascript
let lineMap = [];             // lineMap[lineIdx] = { chunkIdx, localIdx, start_zh, end_zh }
let chunkAudioElements = [];  // <audio> elements, one per chunk
let currentLineIndex = -1;
let currentAudio = null;
let lineEndTimer = null;
let isPlaying = false;
let playbackSpeeds = [0.5, 0.7, 0.85, 1.0];
let currentSpeedIndex = 3;
```

### Audio structure
Each chunk has ONE `<audio>` element for the Chinese recording. Playing a back-chain step means seeking within that audio element and playing for a duration interval.

### How playLine works (lines 556–584)
```javascript
function playLine(lineIndex) {
    const mapping = lineMap[lineIndex];
    const audio = chunkAudioElements[mapping.chunkIdx];
    clearLineEndTimer();
    highlightLine(lineIndex);
    currentLineIndex = lineIndex;

    audio.currentTime = mapping.start_zh;
    audio.play();
    // Poll for end: check audio.currentTime >= mapping.end_zh
    lineEndTimer = setInterval(() => {
        if (audio.currentTime >= mapping.end_zh) {
            clearLineEndTimer();
            advanceToNextLine();
        }
    }, 50);
}
```

### Content rendering (lines 683–720)
```javascript
sentenceDiv.innerHTML = `
    <div class="pinyin">${pinyinSentence}</div>
    <div class="chinese">${chineseSentence}</div>
    <div class="translation">${englishTranslation}</div>
`;
```

### Where to add the back-chain button
Inside the `.chinese` div, prepended before the existing `<span>` tokens:

```javascript
let backchainButtonHtml = '';
if (backchainMap[i]) {
    backchainButtonHtml = `<button class="backchain-btn" data-line-index="${i}">▶</button>`;
}

sentenceDiv.innerHTML = `
    <div class="pinyin">${pinyinSentence}</div>
    <div class="chinese">${backchainButtonHtml}${chineseSentence}</div>
    <div class="translation">${englishTranslation}</div>
`;
```

### Speed control — existing pattern
Speed changes happen via `audio.playbackRate`. The `speedBtn` click handler already updates all `chunkAudioElements`:
```javascript
chunkAudioElements.forEach(audio => {
    audio.playbackRate = currentSpeed;
});
```
Back-chain playback must also read `playbackSpeeds[currentSpeedIndex]` since `Audio.playbackRate` is already set on the shared audio elements.

---

## 10. The Back-Chaining Prompt

**File**: `prompts/back-chaining.md` (already exists)

### Input format
```
我想买水果。
我不是中国人。这是我的东西。
我明天要去图书馆看书。
```

### Output format (newline-separated blocks)
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

### Multi-sentence lines
When an input line has multiple sentences (e.g. "我不是中国人。这是我的东西。"), the output interleaves backchains from both sentences. See the full example in `prompts/back-chaining.md`.

### Critical rules the LLM must follow
1. Keep tone-sandhi pairs together (3-3 → 2-3 cannot be split)
2. Keep fixed structures together (不 + word, 一 + word)
3. Keep breath groups intact (verb-object, time phrases, locations)
4. Final segment should be as short as possible (often 2 chars)
5. Final step of each block must match the original line exactly

---

## 11. Temporary Files and Cleanup

**Pattern**: `create_ereader.py` uses `base_dir / "pipe" / "tokenized"` and `base_dir / "chunks"` dirs. The `base_dir` is `config.book_dir(author, slug)` which resolves to `books/<author>/<slug>`.

For temp files during back-chain generation, use the chunks dir:
```python
backchain_input = chunks_dir / f"{stem}_backchain_input.txt"
backchain_output = chunks_dir / f"{stem}_backchain_output.txt"
```

These should be cleaned up after successful generation.

---

## 12. Backfill Script Requirements

The backfill script (`scripts/backfill_backchain.py`) must:
1. Walk `books/podcasts/<slug>/` directories
2. For each, check if `backchain` data already exists in `chunks/<slug>_1.json`
3. If not, parse the original script from `books_src/podcasts/<slug>.txt` to get dialogue lines
4. Start TTS service (`systemctl --user start qwentts`) for forced alignment
5. Run backchain LLM generation
6. Run forced alignment
7. Write updated chunk JSON
8. Stop TTS service

The existing `systems.manager.ServiceManager` handles service lifecycle. The backfill script should either use it or directly shell out to systemctl.

### Existing example of systemctl usage
From `AGENTS.md`:
```bash
systemctl --user start llamacpp
systemctl --user stop llamacpp
systemctl --user start qwentts
systemctl --user stop qwentts
```

---

## 13. Error Handling Patterns

### LLM output parse failure
Follow the tokenization retry pattern in `create_ereader.py`: log a warning, build a fix prompt with the original input + validation errors, retry up to `config.llm_retries` times.

### Forced alignment failure
If the aligner returns empty words or fails entirely (non-200), log a warning and continue without word-boundary data. The player falls back to proportional timing.

### Missing audio file
If `audio_zh` is missing in a chunk, skip word-boundary extraction for that chunk. This is normal for chapters created with `--skip-audio`.

---

## 14. Logging Conventions

All logging uses the `storyline.logging` module:
```python
from storyline.logging import get_logger
log = get_logger("podcast.backchain")  # or appropriate sub-namespace

log.info("event=backchain_gen lines=%d duration_ms=%d", n_lines, elapsed)
log.warning("event=backchain_validation_error errors=%s", "; ".join(errors))
log.info("event=forced_align chunk=%d words=%d", ci, len(words))
```

Log lines follow `event=<name> key=value ...` structured format.