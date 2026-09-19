"""Backfill grammar breakdown data for existing podcast episodes.

Usage:
    python scripts/backfill_breakdown.py --slug setting-up-a-new-phone
    python scripts/backfill_breakdown.py --all
"""

import argparse
import json
import sys
import time
from pathlib import Path

from storyline.book.breakdown import generate_breakdown_for_chapter
from storyline.config.pipeline_config import PipelineConfig
from storyline.logging import get_logger, init
from storyline.podcast.script_parser import parse_script
from storyline.services.manager import ServiceManager


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BOOKS_SRC = _PROJECT_ROOT / "books_src" / "podcasts"
_BOOKS_DIR = _PROJECT_ROOT / "books" / "podcasts"

log = get_logger("backfill.breakdown")


def _resolve_extra_options(config: PipelineConfig, task: str) -> dict | None:
    budget = config.resolve_thinking_budget(task)
    if budget is not None and budget > 0:
        return {"thinking_budget_tokens": budget}
    return None


def backfill_episode(
    slug: str,
    config: PipelineConfig,
    *,
    service_manager: ServiceManager,
) -> bool:
    """Generate breakdown for an episode that doesn't have it yet."""
    book_dir = _BOOKS_DIR / slug
    chunk_json_path = book_dir / "chunks" / f"{slug}_1.json"
    script_path = _BOOKS_SRC / f"{slug}.txt"

    if not chunk_json_path.exists():
        log.warning("event=skip_no_chunks slug=%s", slug)
        return False

    if not script_path.exists():
        log.warning("event=skip_no_script slug=%s", slug)
        return False

    with open(chunk_json_path, encoding="utf-8") as f:
        chunk_data = json.load(f)

    chunks = chunk_data["chunks"]

    # Check if any chunk already has breakdown data
    has_breakdown = any(
        c.get("lines") and c["lines"][0].get("breakdown")
        for c in chunks
    )
    if has_breakdown:
        log.info("event=skip_has_breakdown slug=%s", slug)
        return True

    script_text = script_path.read_text(encoding="utf-8")
    script = parse_script(script_text)
    dialogue_lines = [line.chinese for line in script.dialogue]
    dialogue_lines = [l for l in dialogue_lines if l.strip()]

    if not dialogue_lines:
        log.warning("event=skip_no_dialogue slug=%s", slug)
        return False

    t0 = time.time()
    service_manager.start_if_needed("llm")
    log.info("event=breakdown_start slug=%s lines=%d", slug, len(dialogue_lines))

    try:
        breakdowns = generate_breakdown_for_chapter(
            dialogue_lines, f"{slug}_1", book_dir,
            resolve_prompt=config.resolve_prompt,
            models=config.models,
            llm_timeout_s=config.llm_timeout_s,
            llm_retries=config.llm_retries,
            extra_options=_resolve_extra_options(config, "translate"),
        )
    except ValueError as e:
        log.error("event=breakdown_failure slug=%s error=%s", slug, str(e))
        return False

    # Store breakdown in chunk JSON
    for ci, chunk in enumerate(chunks):
        line_range = chunk.get("line_range", [])
        if len(line_range) != 2:
            continue
        line_idx = line_range[0]
        if line_idx < len(breakdowns) and breakdowns[line_idx]:
            if chunk.get("lines"):
                chunk["lines"][0]["breakdown"] = breakdowns[line_idx]

    with open(chunk_json_path, "w", encoding="utf-8") as f:
        json.dump(chunk_data, f, ensure_ascii=False, indent=2)

    n_covered = sum(1 for b in breakdowns if b)
    log.info(
        "event=breakdown_complete slug=%s lines=%d covered=%d duration_ms=%d",
        slug, len(dialogue_lines), n_covered, int((time.time() - t0) * 1000),
    )
    return True


def main():
    init()
    parser = argparse.ArgumentParser(
        description="Backfill grammar breakdown data for podcast episodes"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", type=str, help="Process a single episode by slug")
    group.add_argument("--all", action="store_true", help="Process all podcast episodes")
    parser.add_argument(
        "--profile", type=str, default=None,
        help="Pipeline profile from pipeline.toml",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List episodes that need breakdown without processing them",
    )
    args = parser.parse_args()

    config = PipelineConfig.from_files_and_args(args, profile_name=args.profile)
    service_manager = ServiceManager()

    if args.slug:
        slugs = [args.slug]
    else:
        slugs = sorted(
            d.name for d in _BOOKS_DIR.iterdir()
            if d.is_dir() and (d / "chunks" / f"{d.name}_1.json").exists()
        )

    if not slugs:
        log.warning("event=no_episodes_found")
        return 1

    if args.dry_run:
        for slug in slugs:
            chunk_json_path = _BOOKS_DIR / slug / "chunks" / f"{slug}_1.json"
            with open(chunk_json_path, encoding="utf-8") as f:
                chunk_data = json.load(f)
            has_breakdown = any(
                c.get("lines") and c["lines"][0].get("breakdown")
                for c in chunk_data["chunks"]
            )
            status = "has_breakdown" if has_breakdown else "needs_breakdown"
            print(f"{slug}: {status}")
        return 0

    succeeded = 0
    failed = 0
    try:
        for slug in slugs:
            log.info("event=episode_start slug=%s", slug)
            try:
                backfill_episode(slug, config, service_manager=service_manager)
                succeeded += 1
            except Exception as e:
                failed += 1
                log.error("event=episode_failure slug=%s error=%s", slug, str(e))
    finally:
        service_manager.stop("llm")

    log.info(
        "event=backfill_complete total=%d succeeded=%d failed=%d",
        len(slugs), succeeded, failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())