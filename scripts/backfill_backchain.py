"""Backfill back-chain and word-timing data for existing podcast episodes.

Usage:
    python scripts/backfill_backchain.py --slug setting-up-a-new-phone
    python scripts/backfill_backchain.py --all
    python scripts/backfill_backchain.py --all --skip-llm   # only forced alignment
    python scripts/backfill_backchain.py --all --skip-align  # only backchain gen
"""

import argparse
import json
import sys
import time
from pathlib import Path

from pydub import AudioSegment

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.config.pipeline_config import PipelineConfig
from storyline.logging import get_logger, init
from storyline.book.parse_pipe_format import parse_tokenized_file
from storyline.podcast.backchain import generate_backchains, fix_token_boundaries
from storyline.podcast.script_parser import parse_script
from storyline.services.manager import ServiceManager

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_BOOKS_SRC = _PROJECT_ROOT / "books_src" / "podcasts"
_BOOKS_DIR = _PROJECT_ROOT / "books" / "podcasts"

log = get_logger("backfill.backchain")


def _resolve_extra_options(config: PipelineConfig, task: str) -> dict | None:
    budget = config.resolve_thinking_budget(task)
    if budget is not None and budget > 0:
        return {"thinking_budget_tokens": budget}
    return None


def _fix_existing_boundaries(
    chunk_data: dict, token_texts: list[list[str]],
) -> int:
    """Fix backchain boundaries in already-generated chunk data. Returns count of lines changed."""
    from storyline.podcast.backchain import BackchainResult, BackchainStep

    results: list = []
    line_to_chunk: dict[int, int] = {}

    for ci, chunk in enumerate(chunk_data["chunks"]):
        line_range = chunk.get("line_range", [])
        if not line_range:
            continue
        line_idx = line_range[0]
        lines = chunk.get("lines", [])
        if not lines:
            continue
        backchain = lines[0].get("backchain", [])
        if not backchain:
            continue
        if line_idx >= len(token_texts):
            continue

        original = "".join(token_texts[line_idx])
        steps = [BackchainStep(text=t, is_final=False) for t in backchain]
        steps.append(BackchainStep(text=original, is_final=True))
        results.append(BackchainResult(
            line_index=line_idx, original=original, steps=steps,
        ))
        line_to_chunk[line_idx] = ci

    if not results:
        return 0

    fixed = fix_token_boundaries(results, token_texts)

    changed = 0
    for r in fixed:
        ci = line_to_chunk.get(r.line_index)
        if ci is None:
            continue
        old = chunk_data["chunks"][ci]["lines"][0].get("backchain", [])
        new = [s.text for s in r.steps[:-1]]
        if old != new:
            chunk_data["chunks"][ci]["lines"][0]["backchain"] = new
            changed += 1
    return changed


def backfill_episode(
    slug: str,
    config: PipelineConfig,
    *,
    service_manager: ServiceManager,
    skip_llm: bool = False,
    skip_align: bool = False,
) -> bool:
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
    has_backchain = any(
        c.get("lines") and c["lines"][0].get("backchain")
        for c in chunks
    )
    has_words = any(
        c.get("lines") and c["lines"][0].get("words")
        for c in chunks
    )

    # Fix existing backchain boundaries against tokenization (no LLM needed).
    # Write immediately so fixes persist even when skipping further steps.
    token_path = book_dir / "pipe" / "tokenized" / f"{slug}_1.txt"
    tokenized = None
    if has_backchain and token_path.exists():
        tokenized = parse_tokenized_file(token_path)
        token_texts = [[t[0] for t in sent["t"]] for sent in tokenized]
        fixed_count = _fix_existing_boundaries(chunk_data, token_texts)
        if fixed_count:
            log.info("event=backchain_fixed slug=%s fixed_lines=%d", slug, fixed_count)
            with open(chunk_json_path, "w", encoding="utf-8") as f:
                json.dump(chunk_data, f, ensure_ascii=False, indent=2)

    if has_backchain and has_words:
        log.info("event=skip_complete slug=%s", slug)
        return True
    if has_backchain and skip_align:
        log.info("event=skip_has_backchain slug=%s", slug)
        return True
    if has_words and skip_llm:
        log.info("event=skip_has_words slug=%s", slug)
        return True

    script_text = script_path.read_text(encoding="utf-8")
    script = parse_script(script_text)
    dialogue_lines = [line.chinese for line in script.dialogue]
    dialogue_lines = [l for l in dialogue_lines if l.strip()]

    if not has_backchain and not skip_llm:
        t0 = time.time()
        service_manager.start_if_needed("llm")
        log.info("event=backchain_start slug=%s lines=%d", slug, len(dialogue_lines))
        results = generate_backchains(
            dialogue_lines, slug, book_dir,
            resolve_prompt=config.resolve_prompt,
            models=config.models,
            llm_timeout_s=config.llm_timeout_s,
            llm_retries=config.llm_retries,
            extra_options=_resolve_extra_options(config, "backchain"),
        )
        # Fix boundaries against tokenization before storing.
        if tokenized is None and token_path.exists():
            tokenized = parse_tokenized_file(token_path)
        if tokenized:
            token_texts = [[t[0] for t in sent["t"]] for sent in tokenized]
            results = fix_token_boundaries(results, token_texts)
        for r in results:
            if r.line_index < len(chunks):
                chunk = chunks[r.line_index]
                steps = [s.text for s in r.steps[:-1]]
                chunk["lines"][0]["backchain"] = steps
        log.info(
            "event=backchain_complete slug=%s lines=%d duration_ms=%d",
            slug, len(results), int((time.time() - t0) * 1000),
        )

    if not skip_align and not has_words:
        t0 = time.time()
        service_manager.start_if_needed("tts")
        audio_dir = book_dir / "audio"
        aligned = 0
        service = Qwen3TTSService()
        for ci, chunk in enumerate(chunks):
            if chunk.get("lines") and chunk["lines"][0].get("words"):
                continue
            audio_file = chunk.get("audio_zh")
            if not audio_file:
                continue
            audio_path = audio_dir / audio_file
            if not audio_path.exists():
                continue
            line_idx = chunk["line_range"][0]
            if line_idx >= len(script.dialogue):
                continue
            chinese_text = script.dialogue[line_idx].chinese
            if not chinese_text.strip():
                continue
            try:
                audio = AudioSegment.from_file(audio_path)
                words = service.forced_align(audio, chinese_text, "chinese")
                chunk["lines"][0]["words"] = words
                aligned += 1
            except Exception as e:
                log.warning(
                    "event=align_failure slug=%s chunk=%d text=%s error=%s",
                    slug, ci, chinese_text[:40], str(e),
                )
        log.info(
            "event=align_complete slug=%s chunks=%d duration_ms=%d",
            slug, aligned, int((time.time() - t0) * 1000),
        )

    with open(chunk_json_path, "w", encoding="utf-8") as f:
        json.dump(chunk_data, f, ensure_ascii=False, indent=2)

    return True


def main():
    init()
    parser = argparse.ArgumentParser(
        description="Backfill back-chain and word-timing data for podcast episodes"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--slug", type=str, help="Process a single episode by slug")
    group.add_argument("--all", action="store_true", help="Process all podcast episodes")
    parser.add_argument("--skip-llm", action="store_true", help="Skip backchain LLM generation")
    parser.add_argument("--skip-align", action="store_true", help="Skip forced alignment")
    parser.add_argument(
        "--profile", type=str, default=None,
        help="Pipeline profile from pipeline.toml",
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

    need_tts = not args.skip_align
    need_llm = not args.skip_llm

    succeeded = 0
    failed = 0
    try:
        for slug in slugs:
            log.info("event=episode_start slug=%s", slug)
            try:
                backfill_episode(
                    slug, config,
                    service_manager=service_manager,
                    skip_llm=args.skip_llm,
                    skip_align=args.skip_align,
                )
                succeeded += 1
            except Exception as e:
                failed += 1
                log.error("event=episode_failure slug=%s error=%s", slug, str(e))
    finally:
        if need_llm:
            service_manager.stop("llm")
        if need_tts:
            service_manager.stop("tts")

    log.info(
        "event=backfill_complete total=%d succeeded=%d failed=%d",
        len(slugs), succeeded, failed,
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())