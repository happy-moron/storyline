#!/usr/bin/env python3
"""
Backfill missing inverted (dark-mode) flashcard images.

Walks flashcard directories under a podcast root, finds img_*.png files
that have no corresponding img_*_inverted.png, and generates them using
PIL (grayscale + invert).

Usage:
    python scripts/backfill_flashcard_inverted.py
    python scripts/backfill_flashcard_inverted.py --root books/podcasts
    python scripts/backfill_flashcard_inverted.py --dry-run
"""

import argparse
import sys
from pathlib import Path

from PIL import Image, ImageOps

MAX_CARDS = 6


def find_flashcard_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    dirs = []
    for d in sorted(root.iterdir()):
        if d.is_dir():
            fc = d / "flashcards"
            if fc.is_dir():
                dirs.append(fc)
    return dirs


def missing_inverted(flashcard_dir: Path) -> list[Path]:
    missing: list[Path] = []
    for i in range(1, MAX_CARDS + 1):
        original = flashcard_dir / f"img_{i}.png"
        inverted = flashcard_dir / f"img_{i}_inverted.png"
        if original.is_file() and not inverted.is_file():
            missing.append(original)
    return missing


def generate_inverted(original: Path, inverted: Path) -> Path:
    img = Image.open(str(original)).convert("L")
    inverted_img = ImageOps.invert(img)
    inverted_img.save(str(inverted))
    return inverted


def main():
    parser = argparse.ArgumentParser(
        description="Backfill missing inverted flashcard images."
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("books/podcasts"),
        help="Root directory containing per-podcast flashcard dirs",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be done without generating files",
    )
    args = parser.parse_args()

    root = args.root.resolve()
    if not root.is_dir():
        print(f"Root directory not found: {root}", file=sys.stderr)
        sys.exit(1)

    flashcard_dirs = find_flashcard_dirs(root)
    if not flashcard_dirs:
        print(f"No flashcard directories found under {root}")
        sys.exit(0)

    total_missing = 0
    total_generated = 0
    total_skipped = 0

    for fc_dir in flashcard_dirs:
        missing = missing_inverted(fc_dir)
        if not missing:
            continue

        episode = fc_dir.parent.name
        total_missing += len(missing)

        print(f"\n[{episode}] {len(missing)} missing inverted image(s):")
        for original in missing:
            inverted = original.parent / f"{original.stem}_inverted.png"
            if args.dry_run:
                print(f"  [dry-run] {original.name} -> {inverted.name}")
                total_skipped += 1
            else:
                generate_inverted(original, inverted)
                print(f"  generated {inverted.name}")
                total_generated += 1

    if total_missing == 0:
        print("\nAll flashcard directories already have inverted images. Nothing to do.")
    elif args.dry_run:
        print(f"\n[dry-run] {total_missing} inverted image(s) would be generated across {len(flashcard_dirs)} directories.")
    else:
        print(f"\nDone. Generated {total_generated} inverted image(s).")


if __name__ == "__main__":
    main()