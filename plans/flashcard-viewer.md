# Flashcard Viewer

## Original Request

> I'd like to do a bit of planning around the 'flashcard' feature of the podcast generation for this project. There's an existing e-reader which is great for dealing with dialogue. However, there's no feature to review the flashcards (and their audio), a form of which is generated as part of the mp3 generation. I'd like to brainstorm how to integrate in a flashcard viewer that allows flashcards (and their sample sentences) viewed and the audio played. The audio would probably be generated earlier as part of the e-reader phase and then re-used instead of generating it as part of the mp3 creation.
>
> It should be noted, the existing e-reader is also used for books as well as podcasts so the vocab viewing shouldn't disrupt that - perhaps a separate html/js/css page that gets neatly linked somehow?

---

## Context

### What already exists

- **Flashcard data** is generated at `books/podcasts/{theme_slug}/flashcards/` during the podcast pipeline (Stage 1, via `create_podcast.py` → `run_pipeline.py`). See plan: `add-flashcards-to-podcasts.md`.

- **Data format** — `flashcard_entries.json` contains 6 entries, each with 7 fields:

  ```json
  [
    "担心",        // 0: word (simplified Chinese)
    "dānxīn",      // 1: pinyin
    "to worry",    // 2: English definition
    "我很担心你。", // 3: sample sentence (Chinese)
    "Wǒ hěn dānxīn nǐ.", // 4: sample sentence pinyin
    "I am worried about you.", // 5: sample sentence English
    "A face with a worried expression" // 6: image prompt
  ]
  ```

- **Individual text files** (`text_1.txt` through `text_6.txt`) contain the first 6 lines per entry (word through sample sentence English).

- **Image generation + PDF** (`img_*.png`, `cards.pdf`) runs in Stage 2.5 between e-reader audio and MP3 assembly. See plan: `add-flashcards-to-podcasts.md`.

- **Audio in the MP3** — `mp3_gen.py`'s `build_flashcard_intro_sequence()` generates a spoken vocab intro section that is baked into the podcast MP3. Ellen (teacher builtin voice) speaks each word, definition, sample sentence, and translation with repetition. See plan: `flashcard-mp3-gen-updates.md` and `flashcard_pipeline.md`.

### What doesn't exist

- **No persistent audio files** for flashcards. The audio is generated inline during MP3 assembly via `build_flashcard_intro_sequence()` → `_prefetch_host_audio()`. The `AudioSegment` objects go straight into the MP3 concatenation and are never saved to disk as standalone files.

- **No interactive viewer.** Flashcards exist as static files (JSON, text, images, PDF). There is no UI for browsing, flipping, or listening to individual cards.

### Existing reader constraints

The e-reader (`index.html`) is designed around a sentence-by-sentence reading view using chunk-based audio (`lineMap`, `chunkAudioElements`). It works for both books (e.g., Tolstoy) and podcasts. Adding flashcard UI directly to it would:

1. **Clash with the sentence-line model** — flashcards aren't sentences in a chapter
2. **Require conditional DOM/logic** — flashcards only exist for podcasts, not books
3. **Add complexity to an already large single-page app**

Therefore, a **separate HTML/JS/CSS page** is the right call, with a clean link from the reader when flashcards are available.

---

## Design

### 1. Generate persistent audio files during the flashcard pipeline

Each flashcard entry needs **two audio clips**, saved alongside the existing text/JSON files:

| File | Content | Voice |
|------|---------|-------|
| `audio_{N}_word.mp3` | The word itself (zh), e.g. `担心` | Builtin TTS ("Serena") |
| `audio_{N}_sentence.mp3` | The sample sentence (zh), e.g. `我很担心你。` | Builtin TTS ("Serena") |

Directory layout after generation:

```
books/podcasts/{theme_slug}/flashcards/
  flashcard_entries.json     ← existing
  image_prompts.json         ← existing
  text_1.txt ... text_6.txt  ← existing
  audio_1_word.mp3           ← NEW
  audio_1_sentence.mp3       ← NEW
  audio_2_word.mp3           ← NEW
  audio_2_sentence.mp3       ← NEW
  ...
  audio_6_word.mp3
  audio_6_sentence.mp3
```

**Where it goes in the pipeline:** After `_write_text_files_and_collect_prompts()` in `run_pipeline.py`, add a new step `_generate_flashcard_audio(output_dir, entries, service_manager)` that:

1. Ensures TTS service is running
2. For each entry, calls `host_service.generate_audio(word, "zh", speaker="Serena")` and `host_service.generate_audio(sentence, "zh", speaker="Serena")`
3. Exports as MP3 files into `output_dir`

This runs while TTS is already hot from Stage 2 (e-reader audio). The audio is generated once and reused by both the MP3 assembler and the viewer.

**Reuse in MP3 assembly:** `mp3_gen.py`'s `build_flashcard_intro_sequence` can optionally load pre-generated files with `AudioSegment.from_file()` instead of generating live. The current live path should be preserved as a fallback (when audio files aren't found on disk).

**Voice choice:** Using the builtin TTS speaker ("Serena") keeps this simple — no Omnivoice voice cloning required. The builtin voice is already used for the flashcard intro in the MP3, so it's consistent.

### 2. Standalone viewer page (`flashcards.html` + `flashcards.css`)

A separate, self-contained page. Same dark theme as the reader, no build step.

#### UI Layout

```
┌─────────────────────────────────────────────────┐
│  ← Back to Podcast        Card 3 / 6    🔊     │
│                                                  │
│         ┌──────────────────────────┐             │
│         │                          │             │
│         │     担心                  │  ← large    │
│         │     dān xīn              │  ← smaller  │
│         │                          │             │
│         │   ─── (tap/click to      │             │
│         │     flip for English) ─── │             │
│         │                          │             │
│         └──────────────────────────┘             │
│                                                  │
│   Sample:                                        │
│   ┌─────────────────────────────────────┐        │
│   │ 我很担心你。                          │        │
│   │ Wǒ hěn dānxīn nǐ.                    │        │
│   └─────────────────────────────────────┘        │
│                                                  │
│   🔊 Word    🔊 Sentence                        │
│                                                  │
│   ← Prev    ←→ Flip    Next →                    │
└─────────────────────────────────────────────────┘
```

**Flipped state** (card back):

```
│         ┌──────────────────────────┐             │
│         │                          │             │
│         │     to worry             │             │
│         │                          │             │
│         │   ─── (tap/click to      │             │
│         │     flip back to CN) ─── │             │
│         │                          │             │
│         └──────────────────────────┘             │
│                                                  │
│   Sample Translation:                            │
│   ┌─────────────────────────────────────┐        │
│   │ I am worried about you.              │        │
│   └─────────────────────────────────────┘        │
```

#### Features

- **Card flip** — click/tap the card area or press Space to toggle between Chinese front and English back. CSS 3D transform for the flip animation.
- **Audio** — two play buttons: 🔊 Word plays `audio_{N}_word.mp3`, 🔊 Sentence plays `audio_{N}_sentence.mp3`. Both buttons are visible in both flipped and unflipped states.
- **Progress** — "Card 3 / 6" indicator with dots or a thin progress bar.
- **Keyboard shortcuts:**

  | Key | Action |
  |-----|--------|
  | ← / h | Previous card |
  | → / l | Next card |
  | Space | Flip card |
  | w / 1 | Play word audio |
  | s / 2 | Play sentence audio |

- **Back link** — "← Back to Podcast" returns to the reader at the podcast's chapter 1.
- **Data loading** — reads query params: `flashcards.html?author=podcasts&book=going-for-a-hike`, constructs `books/{author}/{book}/flashcards/flashcard_entries.json`, fetches and renders.
- **Audio auto-preload** — preload audio for the current + adjacent cards so playback is instant on navigation.

#### Audio playback behavior

- Starting audio on card N stops any currently playing audio from card N-1
- The sentence audio plays the full clip once (no chunk/sentence-level scrubbing needed — it's just one sentence)
- Playback speed control (same 0.5x/0.7x/0.85x/1.0x as the reader) — reuses the `Audio.playbackRate` approach

#### Edge cases

- **Missing audio files** — if `audio_{N}_word.mp3` doesn't exist (e.g., from an older pipeline run before audio generation was added), the 🔊 buttons are greyed out / disabled
- **Missing flashcard dir** — if `flashcard_entries.json` returns 404, show an error message with a link back to the reader
- **Single card** — prev/next buttons disabled when at boundaries
- **Empty entries** — if json is valid but empty array, show "No flashcards found for this episode"

### 3. Integration: Link from the e-reader

Add a dynamic button to `index.html` that appears only when flashcards exist for the currently loaded podcast.

**Mechanism:** When a book is loaded in the reader, do a quick `fetch` HEAD or GET to `books/{author}/{book}/flashcards/flashcard_entries.json`. If 200 OK, show a "🎴 Flashcards" button in the controls bar.

**Location:** Next to the existing control buttons (← Chapter, ←, ▶, ⏹, →, Chapter →, 🇬🇧, 1x). The button opens `flashcards.html?author={author}&book={book}` in the same tab (or a new tab — TBD).

**Check is cheap** — one small JSON fetch, cached by the browser. Only happens on book load, not on chapter change.

```html
<button id="flashcardBtn" style="display: none;" title="Flashcards" 
        onclick="openFlashcards()">🎴</button>
```

```js
let flashcardAvailable = false;

async function checkFlashcards(author, book) {
    try {
        const resp = await fetch(`books/${author}/${book}/flashcards/flashcard_entries.json`);
        flashcardAvailable = resp.ok;
        document.getElementById('flashcardBtn').style.display = 
            flashcardAvailable ? 'inline-block' : 'none';
    } catch {
        flashcardAvailable = false;
    }
}

function openFlashcards() {
    window.location.href = 
        `flashcards.html?author=${currentState.selectedAuthor}&book=${currentState.selectedBook}`;
}
```

---

## Implementation Plan

Ordered by dependency:

| # | Step | Files |
|---|------|-------|
| 1 | Add `_generate_flashcard_audio()` to the flashcard pipeline — generates `audio_{N}_word.mp3` + `audio_{N}_sentence.mp3` for each entry using the builtin TTS voice | `src/storyline/flashcard/run_pipeline.py` |
| 2 | Integrate audio generation into the podcast pipeline (Stage 2) — call audio gen while TTS is already hot from e-reader audio | `src/storyline/podcast/run_pipeline.py` (or inline in `create_podcast.py`) |
| 3 | Update MP3 assembly to reuse persisted audio files — load from disk when available, fall back to live generation | `src/storyline/podcast/mp3_gen.py` |
| 4 | Create `flashcards.html` — card UI, flip animation, audio playback, keyboard nav, dark theme | new file: `flashcards.html` |
| 5 | Create `flashcards.css` — styling matching the existing reader theme | new file: `flashcards.css` |
| 6 | Add flashcard detection + link button to the reader | `index.html` |
| 7 | Real-world test — run full pipeline, verify audio files on disk, open viewer, test all interactions | test script or manual steps |

---

## Open Questions

- **Host voice:** Use the builtin TTS speaker ("Serena") for flashcard audio, or the cloned podcast character voices? Builtin is simpler — doesn't require Omnivoice and matches the existing MP3 flashcard intro. If a more native feel is desired later, we could add character voice clones as a separate step.

- **Image display:** The flashcard data includes an `image_prompt` field and the pipeline generates `img_*.png` images (used for the printable PDF). Should the viewer show these images on the card front? This would require images to be pre-generated (needs ComfyUI service). Without images, the viewer is text + audio only — still useful for review.

- **Spaced repetition / scoring:** Is this a simple linear review viewer (prev/next cards), or should it include SRS-style scoring (Easy / Hard / Again buttons that reorder/shuffle cards)? A scoring system could be added later as an enhancement — the first version can be linear review.

- **New tab vs. same tab:** When clicking "🎴 Flashcards" in the reader, should it open in a new tab (preserving reader state) or navigate in the same tab? New tab is probably better so the user can easily go back to where they were in the dialogue.