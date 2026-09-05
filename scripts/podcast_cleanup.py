#!/usr/bin/env python3
import argparse
import csv
import os
import shutil
import sys
from pathlib import Path

_DEFAULT_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_EPISODES_HEADER = "Podcast ID, HSK Point, Theme"


def _resolve_path(arg_value: str | None, default_relative: str, project_root: Path) -> Path:
    if arg_value:
        return Path(os.path.expanduser(arg_value))
    return project_root / default_relative


def _mp3_slugs(mp3_dir: Path) -> set[str]:
    if not mp3_dir.is_dir():
        return set()
    return {p.stem for p in mp3_dir.glob("*.mp3")}


def _load_csv(path: Path) -> tuple[list[str], list[dict]]:
    if not path.exists() or path.stat().st_size == 0:
        return [_EPISODES_HEADER], []

    fieldnames: list[str] = []
    rows: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, skipinitialspace=True)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    header = ", ".join(fieldnames) if fieldnames else _EPISODES_HEADER
    return [header], rows


def _theme_slugs_from_csv(rows: list[dict]) -> set[str]:
    slugs: set[str] = set()
    for row in rows:
        theme = (row.get("Theme") or "").strip()
        if theme:
            slugs.add(theme)
    return slugs


def _write_csv(path: Path, header: list[str], rows: list[dict]) -> None:
    fieldnames = [f.strip() for f in header[0].split(",")]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _cleanup_artifacts(
    slug: str,
    script_dir: Path,
    vocab_dir: Path,
    books_dir: Path,
    dry_run: bool,
) -> list[str]:
    removed: list[str] = []

    script_path = script_dir / f"{slug}.txt"
    if script_path.exists():
        if not dry_run:
            script_path.unlink()
        removed.append(str(script_path))

    vocab_path = vocab_dir / f"{slug}.txt"
    if vocab_path.exists():
        if not dry_run:
            vocab_path.unlink()
        removed.append(str(vocab_path))

    ereader_dir = books_dir / slug
    if ereader_dir.is_dir():
        n_files = sum(1 for _ in ereader_dir.rglob("*") if _.is_file())
        if not dry_run:
            shutil.rmtree(ereader_dir)
        removed.append(f"{ereader_dir}/ ({n_files} files)")

    flashcards_dir = books_dir / slug / "flashcards"
    if flashcards_dir.is_dir():
        n_files = sum(1 for _ in flashcards_dir.rglob("*") if _.is_file())
        if not dry_run:
            shutil.rmtree(flashcards_dir)
        removed.append(f"{flashcards_dir}/ ({n_files} files)")

    return removed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean up orphaned artifacts from failed podcast episodes"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be removed without actually removing anything",
    )
    parser.add_argument(
        "--project-root", type=str, default=str(_DEFAULT_PROJECT_ROOT),
        help="Project root directory",
    )
    parser.add_argument(
        "--mp3-dir", type=str, default=None,
        help="Directory of final MP3 files (default: ~/temp/audio/podcasts)",
    )
    parser.add_argument(
        "--script-dir", type=str, default=None,
        help="Script .txt directory (default: <project_root>/books_src/podcasts)",
    )
    parser.add_argument(
        "--vocab-dir", type=str, default=None,
        help="Vocab .txt directory (default: <project_root>/books_src/podcasts/vocab)",
    )
    parser.add_argument(
        "--books-dir", type=str, default=None,
        help="E-reader artifacts directory (default: <project_root>/books/podcasts)",
    )
    parser.add_argument(
        "--episodes-path", type=str, default=None,
        help="Path to existing-episodes.csv",
    )
    args = parser.parse_args()

    project_root = Path(args.project_root)

    mp3_dir = _resolve_path(
        args.mp3_dir, os.path.expanduser("~/temp/audio/podcasts"), project_root,
    )
    script_dir = _resolve_path(args.script_dir, "books_src/podcasts", project_root)
    vocab_dir = _resolve_path(args.vocab_dir, "books_src/podcasts/vocab", project_root)
    books_dir = _resolve_path(args.books_dir, "books/podcasts", project_root)
    episodes_path = _resolve_path(
        args.episodes_path, "src/storyline/podcast/existing-episodes.csv", project_root,
    )

    complete = _mp3_slugs(mp3_dir)
    header, all_rows = _load_csv(episodes_path)
    csv_slugs = _theme_slugs_from_csv(all_rows)

    if not csv_slugs:
        print("No episodes found in CSV. Nothing to do.")
        return

    partial_slugs = csv_slugs - complete
    if not partial_slugs:
        print(f"All {len(csv_slugs)} episodes in CSV have MP3s. Nothing to clean up.")
        return

    print(f"Complete episodes (have MP3): {len(complete)}")
    print(f"Partial episodes (no MP3):    {len(partial_slugs)}")
    if args.dry_run:
        print("[DRY RUN] -- no files will be removed\n")
    else:
        print()

    total_removed = 0
    for slug in sorted(partial_slugs):
        removed = _cleanup_artifacts(slug, script_dir, vocab_dir, books_dir, args.dry_run)
        total_removed += len(removed)
        for path in removed:
            print(f"  rm {path}")
        if not removed:
            print(f"  {slug}: no artifacts found")

    kept_rows = [r for r in all_rows if (r.get("Theme") or "").strip() not in partial_slugs]
    removed_row_count = len(all_rows) - len(kept_rows)

    if not args.dry_run:
        _write_csv(episodes_path, header, kept_rows)

    print()
    print(f"Artifacts removed: {total_removed}")
    print(f"CSV rows removed:  {removed_row_count}")
    print(f"Episodes cleaned:  {len(partial_slugs)}")
    if args.dry_run:
        print(f"CSV would be rewritten: {episodes_path} ({len(kept_rows)} rows remaining)")
    else:
        print(f"CSV rewritten: {episodes_path} ({len(kept_rows)} rows remaining)")


if __name__ == "__main__":
    main()