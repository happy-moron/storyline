"""End-to-end podcast pipeline runner.

Usage:
    python -m storyline.podcast.run_pipeline           # all three stages
    python -m storyline.podcast.run_pipeline --skip-audio  # skip dialogue audio
"""

import argparse
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


def run_full_pipeline(
    config: PipelineConfig,
    service_manager: ServiceManager | None = None,
    *,
    skip_audio: bool = False,
    use_builtin_hosts: bool = False,
) -> Path:
    storyline.logging.init()
    log = get_logger("podcast.pipeline")
    t_start = time.time()

    # ── Stage 1 — Script ───────────────────────────────────────────────
    log.info("event=pipeline_stage stage=1 script")
    t0 = time.time()
    selection, vocab_output, script_output = generate_podcast(
        config, service_manager=service_manager,
    )
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
    create_ereader(script_path, config, service_manager=service_manager)
    log.info(
        "event=pipeline_stage_complete stage=2 duration_ms=%d",
        int((time.time() - t0) * 1000),
    )

    # ── Stage 3 — MP3 ──────────────────────────────────────────────────
    log.info("event=pipeline_stage stage=3 mp3")
    t0 = time.time()
    mp3_path = create_mp3(script_path, config, service_manager=service_manager, use_builtin_hosts=use_builtin_hosts)
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
    args = parser.parse_args()

    config = PipelineConfig.from_files_and_args(args, profile_name=args.profile)
    service_manager = ServiceManager()
    try:
        run_full_pipeline(
            config, service_manager=service_manager,
            skip_audio=args.skip_audio,
            use_builtin_hosts=args.builtin_hosts,
        )
    finally:
        if config.llm_provider == "local":
            service_manager.stop("llm")
        service_manager.stop("tts")