# Flashcard Vocab Manifest — Design

## Current State

Flashcards for each podcast episode live in `books/podcasts/{episode}/flashcards/`:

```
books/podcasts/{episode}/flashcards/
├── flashcard_entries.json       # [[word, pinyin, def, sent_cn, sent_py, sent_en, img_prompt], ...] (6 entries)
├── image_prompts.json           # [prompt1, ...] (6 prompts)
├── text_1.txt .. text_6.txt     # each: 6 lines (word .. sent_en)
├── audio_1_word.mp3 .. audio_6_word.mp3
├── audio_1_sentence.mp3 .. audio_6_sentence.mp3
├── img_1.png .. img_6.png
├── img_1_inverted.png .. img_6_inverted.png
└── cards.pdf
```

**Scope of duplication** (current analysis):
- **274** unique words across **55** episodes
- **45** words appear in **2+** episodes (e.g. 准备 in 8 episodes)
- Each duplicated word means duplicated audio, images, text files

## Goals

1. **Single manifest** — a unified index of all flashcards across every book, keyed by Simplified Chinese word
2. **Deduplicated assets** — audio and image files stored once per word in a shared location
3. **Per-book card order preserved** — each episode still knows its 6 words (in order)
4. **Backward-compatible HTML** — `index.html` and `flashcards.html` updated to read from manifest
5. **Pipeline updated** — new episode generation writes to both shared and per-episode locations
6. **Migration path** — script to build the initial manifest and reorganize existing assets

## Design

### 1. New Manifest File: `books/flashcard-manifest.json`

A single JSON file at the project root under `books/`.

```json
{
  "version": 1,
  "words": {
    "准备": {
      "pinyin": "zhǔnbèi",
      "definition": "to prepare",
      "canonical_source": "acting-too-slowly",
      "image_prompt": "A person preparing ingredients in a kitchen",
      "sources": ["acting-too-slowly", "chinese-new-years-celebration", "going-for-a-hike", "going-out-in-the-rain", "meeting-the-in-laws", "mid-autumn-festival", "planning-a-birthday-party", "settling-into-work-at-the-office"]
    }
  },
  "books": {
    "acting-too-slowly": ["准备", "慢", "煮", "刚才", "时间", "客人"],
    "chinese-new-years-celebration": ["准备", "饺子", "春节", "忙", "聚会", "欢迎"],
    ...
  }
}
```

Design decisions:
- **`canonical_source`**: the first episode that contributed this word (first-in-wins). Its sample sentence, pinyin, definition, and image prompt become the canonical entry.
- **`sources`**: all episodes that contain this word, for cross-linking.
- **`books`**: maps book slug → ordered list of word keys. This preserves the per-episode ordering needed for PDF generation and flashcard navigation.

**NOT included in canonical manifest (remains per-episode):**
- The sample sentences — these are context-specific to each episode. However, for the reader/flashcard UI, we show the canonical one. If we want per-episode sentences later, we can extend.

### 2. Deduplicated Asset Store: `books/vocab/`

```
books/vocab/
├── audio/
│   ├── 准备_word.mp3
│   ├── 准备_sentence.mp3
│   ├── 慢_word.mp3
│   ├── 慢_sentence.mp3
│   └── ...
├── images/
│   ├── 准备.png
│   ├── 准备_inverted.png
│   ├── 慢.png
│   ├── 慢_inverted.png
│   └── ...
└── texts/
    ├── 准备.txt
    ├── 慢.txt
    └── ...
```

The text files contain the canonical 6 lines (word, pinyin, def, sentence_cn, sentence_py, sentence_en) matching the canonical entry from the manifest.

### 3. Per-Episode Directory (preserved but simplified)

The per-episode `flashcards/` directory remains for PDF generation:

```
books/podcasts/{episode}/flashcards/
├── cards.pdf            # still generated per-episode
├── flashcard_entries.json  # maintained as a convenience copy/symlink? Or removed?
```

For maximum backward compat, we can keep a slim `flashcard_entries.json` per episode that just lists the 6 word keys. Or we can remove it and have PDF generation read from the manifest + `books` ordering.

**Recommendation**: Keep `flashcard_entries.json` per episode as a simple 6-element array of word strings (no full entry data), for PDF generation to continue working. Remove all audio, image, and text files from the per-episode dir once migrated.

### 4. Pipeline Changes (`run_pipeline.py`)

After flashcard generation for a new episode:

1. **Extract** vocab (unchanged — generates 6 entries with all data)
2. **Deduplicate**: for each of the 6 words, check if it already exists in the manifest:
   - If **new word**: add to manifest's `words` with `canonical_source = current_episode`. Copy audio/images/texts to `books/vocab/`.
   - If **existing word**: add `current_episode` to its `sources` list. Do NOT overwrite assets (first-in-wins).
3. **Update manifest**: add the 6-word ordered list to `books.{episode}`.
4. **Generate images** (unchanged — runs per-episode prompts, but only copies new images to shared store).
5. **Generate PDF** (unchanged — still reads from per-episode dir, or updated to read from shared store).

### 5. Migration Script

A one-time script `tools/migrate_flashcards.py`:

1. Iterate all `books/podcasts/*/flashcards/flashcard_entries.json`
2. Build manifest with first-in-wins dedup
3. Copy audio/images/texts to `books/vocab/` (first wins, skip if exists)
4. Write manifest
5. Optionally, clean up per-episode audio/image/text files (or leave as-is)

### 6. HTML Changes

#### `index.html`

**`checkFlashcards()`** — currently does a HEAD fetch per episode. Change to:

```js
async function checkFlashcards() {
    if (!manifest) await loadManifest();
    const book = currentState.selectedBook;
    if (!book) { flashcardBtn.style.display = 'none'; return; }
    // Load the flashcard manifest once
    if (!flashcardManifest) {
        try {
            flashcardManifest = await fetch('books/flashcard-manifest.json').then(r => r.json());
        } catch { flashcardBtn.style.display = 'none'; return; }
    }
    const hasFlashcards = flashcardManifest.books && flashcardManifest.books[book];
    flashcardBtn.style.display = hasFlashcards ? 'inline-block' : 'none';
    flashcardAvailable = !!hasFlashcards;
}
```

**`openFlashcards()`** — unchanged (passes author/book via URL params).

#### `flashcards.html`

**`loadEntries()`** — currently loads `books/{author}/{book}/flashcards/flashcard_entries.json`. Change to:

1. Load `books/flashcard-manifest.json` once (cached)
2. Look up `manifest.books[book]` to get the ordered word list
3. For each word, look up `manifest.words[word]` for pinyin, definition
4. For sample sentence and image prompt, fetch from the text file in `books/vocab/texts/{word}.txt` (or embed in manifest)

**Asset paths** change from:
- `books/{author}/{book}/flashcards/audio_{idx}_word.mp3` → `books/vocab/audio/{word}_word.mp3`
- `books/{author}/{book}/flashcards/img_{idx}_inverted.png` → `books/vocab/images/{word}_inverted.png`

**Render loop** uses word key instead of index for asset lookup.

### 7. Benefits

| Metric | Before | After |
|---|---|---|
| Total files on disk | ~336×4 + 336 text files ≈ 1,680 | ~274×4 + 274 text files ≈ 1,370 (22% reduction) |
| Duplicate management | None (manual) | Automatic (first-in-wins) |
| Cross-episode vocab search | Not possible | Via `sources` array |
| Manifest size | ~336 entries (per-episode) | 1 unified manifest |
| Adding new word | Blind duplication | Auto-dedup |
| Reader flashcard check | HEAD request per episode | Single manifest load |

### 8. Future Considerations

- **Cross-book support**: the manifest's `books` key uses slug, so `childrens/my-big-girl-potty` and `tolstoy/where-love-is-there-is-God-also` can be added when they get flashcards.
- **Per-episode sentences**: If we want to show the context-specific sample sentence for each episode, we could extend `words.{word}.sentences` to be a map of `{episode_slug: {cn, py, en}}`. This would add complexity but preserve full context.
- **Dictionary integration**: The manifest could be linked with `dict/custom_dict.json` for richer lookup in the reader.