#!/usr/bin/env python3
"""
One-time migration: build books/flashcard-manifest.json and copy assets
to books/vocab/{audio,images,texts}/ with first-in-wins dedup.
"""
import json
import shutil
from pathlib import Path
from collections import OrderedDict

BOOKS_DIR = Path("books")
PODCASTS_DIR = BOOKS_DIR / "podcasts"
VOCAB_DIR = BOOKS_DIR / "vocab"
MANIFEST_PATH = BOOKS_DIR / "flashcard-manifest.json"

# Each entry: (subdir, src_template_with_idx, dst_filename_template_with_word)
_ASSET_MAP = [
    ("audio",   "audio_{}_word.mp3",       "{}_word.mp3"),
    ("audio",   "audio_{}_sentence.mp3",    "{}_sentence.mp3"),
    ("images",  "img_{}.png",              "{}.png"),
    ("images",  "img_{}_inverted.png",      "{}_inverted.png"),
    ("texts",   "text_{}.txt",             "{}.txt"),
]

VERSION = 1


def _episode_paths() -> list[tuple[str, Path]]:
    """Return sorted (episode_slug, flashcards_dir) for all episodes."""
    results = []
    for d in sorted(PODCASTS_DIR.iterdir()):
        if not d.is_dir():
            continue
        fc_dir = d / "flashcards"
        if not fc_dir.is_dir():
            continue
        if not (fc_dir / "flashcard_entries.json").is_file():
            continue
        results.append((d.name, fc_dir))
    return results


def _load_entries(fc_dir: Path) -> list[list[str]]:
    with open(fc_dir / "flashcard_entries.json", encoding="utf-8") as f:
        return json.load(f)


def _copy_assets(fc_dir: Path, episode: str, idx: int, word: str, vocab_dir: Path):
    """Copy asset files for one entry if they don't already exist in vocab_dir."""
    for subdir, src_tpl, dst_tpl in _ASSET_MAP:
        dest_sub = vocab_dir / subdir
        dest_sub.mkdir(parents=True, exist_ok=True)
        src = fc_dir / src_tpl.format(idx)
        dst = dest_sub / dst_tpl.format(word)
        if src.is_file() and not dst.is_file():
            shutil.copy2(str(src), str(dst))


def _write_slim_entries(fc_dir: Path, words: list[str]):
    """Replace per-episode flashcard_entries.json with a simple word list."""
    with open(fc_dir / "flashcard_entries.json", "w", encoding="utf-8") as f:
        json.dump(words, f, ensure_ascii=False, indent=2)


def build_manifest() -> dict:
    manifest: dict = {
        "version": VERSION,
        "words": OrderedDict(),
        "books": OrderedDict(),
    }

    for episode, fc_dir in _episode_paths():
        entries = _load_entries(fc_dir)
        if len(entries) != 6:
            print(f"  SKIP {episode}: expected 6 entries, got {len(entries)}")
            continue

        manifest["books"][episode] = []
        for idx, entry in enumerate(entries):
            if len(entry) < 7:
                print(f"  SKIP {episode} entry {idx+1}: expected 7 fields, got {len(entry)}")
                continue

            word = entry[0]
            pinyin = entry[1]
            definition = entry[2]
            sent_cn = entry[3]
            sent_py = entry[4]
            sent_en = entry[5]
            img_prompt = entry[6]

            manifest["books"][episode].append(word)

            if word not in manifest["words"]:
                manifest["words"][word] = {
                    "pinyin": pinyin,
                    "definition": definition,
                    "sentence_cn": sent_cn,
                    "sentence_py": sent_py,
                    "sentence_en": sent_en,
                    "image_prompt": img_prompt,
                    "canonical_source": episode,
                    "sources": [episode],
                }
            else:
                manifest["words"][word]["sources"].append(episode)

    return manifest


def copy_all_assets(manifest: dict, vocab_dir: Path):
    """Copy asset files for all canonical word entries."""
    for episode, fc_dir in _episode_paths():
        entries = _load_entries(fc_dir)
        if len(entries) != 6:
            continue

        for idx, entry in enumerate(entries):
            word = entry[0]

            # Only copy for canonical source (first-in-wins)
            wd = manifest["words"].get(word)
            if wd and wd["canonical_source"] != episode:
                continue

            _copy_assets(fc_dir, episode, idx + 1, word, vocab_dir)
            print(f"  {word} <- {episode}")


def write_manifest(manifest: dict):
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"Wrote {MANIFEST_PATH}")


def main():
    print("=== Building flashcard manifest ===")
    manifest = build_manifest()
    print(f"Words: {len(manifest['words'])}")
    print(f"Books: {len(manifest['books'])}")
    write_manifest(manifest)

    print("\n=== Copying assets to books/vocab/ ===")
    copy_all_assets(manifest, VOCAB_DIR)

    print("\nDone.")


if __name__ == "__main__":
    main()