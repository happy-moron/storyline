#!/usr/bin/env python3
"""Fix existing podcast backchain data to align with tokenized word boundaries.

Walks books/podcasts/<slug>/ directories, reads each episode's chunk JSON
together with its tokenized pipe file, applies ``fix_token_boundaries`` to
shift any backchain steps that start mid-token, and writes the updated JSON.

Usage:
    python scripts/fix_backchain_boundaries.py --dry-run
    python scripts/fix_backchain_boundaries.py
    python scripts/fix_backchain_boundaries.py --slug ordering-food-at-a-restaurant
"""

import argparse
import json
import sys
from pathlib import Path

from storyline.book.parse_pipe_format import parse_tokenized_file
from storyline.podcast.backchain import (
    BackchainResult,
    BackchainStep,
    fix_token_boundaries,
)


def find_episodes(books_dir: Path, slug: str | None = None) -> list[Path]:
    episodes: list[Path] = []
    for d in sorted(books_dir.iterdir()):
        if not d.is_dir():
            continue
        if slug and d.name != slug:
            continue
        chunk_json = d / "chunks" / f"{d.name}_1.json"
        token_path = d / "pipe" / "tokenized" / f"{d.name}_1.txt"
        if chunk_json.exists() and token_path.exists():
            episodes.append(d)
    return episodes


def fix_episode(slug_dir: Path, dry_run: bool = False, verbose: bool = False) -> int:
    """Fix one episode. Returns number of lines changed."""
    slug = slug_dir.name
    chunk_json_path = slug_dir / "chunks" / f"{slug}_1.json"
    token_path = slug_dir / "pipe" / "tokenized" / f"{slug}_1.txt"

    with open(chunk_json_path, encoding="utf-8") as f:
        chunk_data = json.load(f)
    chunks = chunk_data["chunks"]

    tokenized = parse_tokenized_file(token_path)
    token_texts = [[t[0] for t in sent["t"]] for sent in tokenized]

    results: list[BackchainResult] = []
    line_idx_to_chunk_idx: dict[int, int] = {}

    for ci, chunk in enumerate(chunks):
        line_range = chunk.get("line_range", [])
        if not line_range or line_range[0] < 0:
            continue
        line_idx = line_range[0]
        lines = chunk.get("lines", [])
        if not lines:
            continue
        backchain = lines[0].get("backchain", [])
        if not backchain:
            continue

        if line_idx >= len(token_texts):
            print(f"  WARN: {slug} line {line_idx} OOB "
                  f"(tokenized has {len(token_texts)} lines)", file=sys.stderr)
            continue

        original = "".join(token_texts[line_idx])

        steps = [BackchainStep(text=t, is_final=False) for t in backchain]
        steps.append(BackchainStep(text=original, is_final=True))

        results.append(BackchainResult(
            line_index=line_idx,
            original=original,
            steps=steps,
        ))
        line_idx_to_chunk_idx[line_idx] = ci

    if not results:
        return 0

    fixed = fix_token_boundaries(results, token_texts)

    changes: list[tuple[int, list[str], list[str]]] = []
    for r in fixed:
        chunk_idx = line_idx_to_chunk_idx.get(r.line_index)
        if chunk_idx is None:
            continue
        old_steps = chunks[chunk_idx]["lines"][0].get("backchain", [])
        new_steps = [s.text for s in r.steps[:-1]]
        if old_steps != new_steps:
            changes.append((r.line_index, old_steps, new_steps))

    if not changes:
        return 0

    if dry_run:
        print(f"  {slug}: would fix {len(changes)} line(s)")
        if verbose:
            for line_idx, old, new in changes:
                print(f"    line {line_idx}: {len(old)}→{len(new)} steps")
                removed = set(old) - set(new)
                added = set(new) - set(old)
                for s in removed:
                    print(f"      - \"{s}\"")
                for s in added:
                    print(f"      + \"{s}\"")
    else:
        for line_idx, _old, new in changes:
            chunk_idx = line_idx_to_chunk_idx[line_idx]
            chunks[chunk_idx]["lines"][0]["backchain"] = new
        with open(chunk_json_path, "w", encoding="utf-8") as f:
            json.dump(chunk_data, f, ensure_ascii=False, indent=2)
        print(f"  {slug}: fixed {len(changes)} line(s)")
        if verbose:
            for line_idx, old, new in changes:
                print(f"    line {line_idx}: {len(old)}→{len(new)} steps")
                removed = set(old) - set(new)
                added = set(new) - set(old)
                for s in removed:
                    print(f"      - \"{s}\"")
                for s in added:
                    print(f"      + \"{s}\"")

    return len(changes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fix backchain boundaries to align with tokenized words",
    )
    parser.add_argument(
        "--slug", type=str, default=None,
        help="Process only the given episode slug",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without writing",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Show per-line diff of backchain steps",
    )
    parser.add_argument(
        "--books-dir", type=str, default="books/podcasts",
        help="Path to podcast books directory (default: books/podcasts)",
    )
    args = parser.parse_args()

    books_dir = Path(args.books_dir)
    if not books_dir.is_dir():
        print(f"ERROR: books directory not found: {books_dir}", file=sys.stderr)
        sys.exit(1)

    episodes = find_episodes(books_dir, args.slug)
    if not episodes:
        print("No episodes found with both chunk JSON and tokenized files.")
        sys.exit(0)

    mode = "dry-run" if args.dry_run else "fix"
    print(f"Found {len(episodes)} episode(s) — running in {mode} mode\n")

    total_changes = 0
    for ep in episodes:
        changes = fix_episode(ep, dry_run=args.dry_run, verbose=args.verbose)
        total_changes += changes

    verb = "would be" if args.dry_run else ""
    if not args.dry_run and total_changes > 0:
        verb = ""
    label = "changed" if total_changes != 1 else "change"
    print(f"\n{total_changes} line(s) {verb} {label} across all episodes")


if __name__ == "__main__":
    main()