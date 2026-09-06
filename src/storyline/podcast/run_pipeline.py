"""End-to-end podcast pipeline runner.

Usage:
    python -m storyline.podcast.run_pipeline           # all three stages
    python -m storyline.podcast.run_pipeline --skip-audio  # skip dialogue audio
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import storyline.logging
from storyline.config.pipeline_config import PipelineConfig
from storyline.logging import get_logger
from storyline.podcast.create_ereader import create_ereader
from storyline.podcast.create_mp3 import create_mp3
from storyline.podcast.create_podcast import generate_podcast
from storyline.services.manager import ServiceManager


def _cleanup_orphan_omnivoice():
    try:
        result = subprocess.run(
            ["pgrep", "-f", r"omnivoice.*(infer|batch)"],
            capture_output=True, text=True, timeout=5,
        )
        pids = [int(pid) for pid in result.stdout.strip().split("\n") if pid]
        for pid in pids:
            if pid == os.getpid():
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    except Exception:
        pass


def run_full_pipeline(
    config: PipelineConfig,
    service_manager: ServiceManager | None = None,
    *,
    skip_audio: bool = False,
    use_builtin_hosts: bool = False,
    use_omnivoice: bool = False,
    skip_flashcards: bool = False,
) -> Path:
    storyline.logging.init()
    log = get_logger("podcast.pipeline")
    t_start = time.time()

    # ── Stage 1 — Script ───────────────────────────────────────────────
    log.info("event=pipeline_stage stage=1 script")
    t0 = time.time()
    result = generate_podcast(
        config, service_manager=service_manager,
        flashcards=not skip_flashcards,
    )
    selection = result["selection"]
    script_output = result["script_output"]
    script_path = str(script_output)
    log.info(
        "event=pipeline_stage_complete stage=1 episode=%s theme=%s duration_ms=%d",
        selection.episode_id, selection.theme_slug,
        int((time.time() - t0) * 1000),
    )

    # ── Stage 2 — E-reader ─────────────────────────────────────────────
    config.skip_audio = skip_audio
    log.info("event=pipeline_stage stage=2 ereader")
    t0 = time.time()
    create_ereader(
        script_path, config, service_manager=service_manager,
        use_omnivoice=use_omnivoice,
    )
    log.info(
        "event=pipeline_stage_complete stage=2 duration_ms=%d",
        int((time.time() - t0) * 1000),
    )

    # ── Generate flashcard audio ──
    if not skip_flashcards:
        flashcard_dir = result.get("flashcard_dir")
        if flashcard_dir and (flashcard_dir / "flashcard_entries.json").is_file():
            log.info("event=pipeline_stage stage=2a flashcard_audio")
            t0 = time.time()
            from storyline.flashcard.run_pipeline import generate_flashcard_audio

            with open(flashcard_dir / "flashcard_entries.json", encoding="utf-8") as f:
                entries = json.load(f)

            if service_manager is not None:
                service_manager.start_if_needed("tts")
            generate_flashcard_audio(flashcard_dir, entries, service_manager)
            if service_manager is not None:
                service_manager.stop("tts")

            log.info(
                "event=pipeline_stage_complete stage=2a duration_ms=%d",
                int((time.time() - t0) * 1000),
            )

    # ── Stage 2.5 — Flashcards (image gen + PDF) ───────────────────────
    if not skip_flashcards:
        flashcard_dir = result.get("flashcard_dir")
        if flashcard_dir and (flashcard_dir / "flashcard_entries.json").is_file():
            log.info("event=pipeline_stage stage=2.5 flashcards_image")
            t0 = time.time()
            try:
                from storyline.flashcard.run_pipeline import generate_flashcard_images_and_pdf

                pdf_path = generate_flashcard_images_and_pdf(
                    flashcard_dir, service_manager,
                )
            finally:
                if service_manager is not None:
                    service_manager.stop("image_gen")

            log.info(
                "event=pipeline_stage_complete stage=2.5 pdf=%s duration_ms=%d",
                pdf_path, int((time.time() - t0) * 1000),
            )
        else:
            log.warning("event=flashcard_skip_no_entries dir=%s", flashcard_dir)

    # ── Stage 3 — MP3 ──────────────────────────────────────────────────
    log.info("event=pipeline_stage stage=3 mp3")
    t0 = time.time()
    mp3_path = create_mp3(
        script_path, config, service_manager=service_manager,
        use_builtin_hosts=use_builtin_hosts, use_omnivoice=use_omnivoice,
        flashcard_dir=result.get("flashcard_dir"),
    )
    log.info(
        "event=pipeline_stage_complete stage=3 duration_ms=%d",
        int((time.time() - t0) * 1000),
    )

    log.info(
        "event=pipeline_complete episode=%s total_ms=%d mp3=%s",
        selection.episode_id, int((time.time() - t_start) * 1000), mp3_path,
    )
    return mp3_path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate a full podcast episode (script → e-reader → mp3)"
    )
    parser.add_argument(
        "-n", "--num-episodes", type=int, default=1,
        help="Number of podcast episodes to attempt (default: 1)",
    )
    parser.add_argument(
        "--profile", type=str, default=None,
        help="Pipeline profile from pipeline.toml",
    )
    parser.add_argument(
        "-m", "--models", type=str, default=None,
        help="Comma-separated list of models (overrides config)",
    )
    parser.add_argument(
        "--skip-audio", action="store_true",
        help="Skip dialogue sentence audio generation (stage 2 TTS)",
    )
    parser.add_argument(
        "--builtin-hosts", action="store_true",
        help="Use built-in Qwen3 voices (Serena for teacher, Eric for student) instead of voice clone",
    )
    parser.add_argument(
        "--omnivoice", action="store_true",
        help="Use omnivoice-infer (CUDA/GPU) for voice cloning instead of Qwen3-TTS service",
    )
    parser.add_argument(
        "--skip-flashcards", action="store_true",
        help="Skip flashcard generation (vocab extraction, image gen, PDF)",
    )
    args = parser.parse_args()

    config = PipelineConfig.from_files_and_args(args, profile_name=args.profile)
    service_manager = ServiceManager()
    log = get_logger("podcast.batch")

    successes = 0
    failures = 0
    try:
        for i in range(args.num_episodes):
            log.info("event=batch_attempt attempt=%d/%d", i + 1, args.num_episodes)
            try:
                run_full_pipeline(
                    config, service_manager=service_manager,
                    skip_audio=args.skip_audio,
                    use_builtin_hosts=args.builtin_hosts,
                    use_omnivoice=args.omnivoice,
                    skip_flashcards=args.skip_flashcards,
                )
                successes += 1
                log.info("event=batch_success attempt=%d/%d", i + 1, args.num_episodes)
            except Exception as e:
                failures += 1
                log.error(
                    "event=batch_failure attempt=%d/%d error=%s",
                    i + 1, args.num_episodes, str(e),
                )
    finally:
        service_manager.stop("llm")
        service_manager.stop("tts")
        _cleanup_orphan_omnivoice()

    log.info(
        "event=batch_complete attempted=%d successes=%d failures=%d",
        args.num_episodes, successes, failures,
    )