import time
from pathlib import Path

import storyline.logging
from storyline.logging import get_logger
from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.config.pipeline_config import PipelineConfig
from storyline.podcast.audio_gen import voice_profile_stem
from storyline.podcast.create_ereader import _slug_from_filename, _title_from_slug
from storyline.podcast.mp3_gen import assemble_podcast_mp3
from storyline.podcast.script_parser import parse_script
from storyline.services.manager import ServiceManager

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_VOICES_DIR = _PROJECT_ROOT / "voices"


def create_mp3(
    script_path: str,
    config: PipelineConfig,
    service_manager: ServiceManager | None = None,
    *,
    use_builtin_hosts: bool = False,
):
    storyline.logging.init()
    log = get_logger("podcast.mp3")

    slug = _slug_from_filename(script_path)
    title = _title_from_slug(slug)
    script_text = Path(script_path).read_text(encoding="utf-8")
    script = parse_script(script_text)

    missing_speakers = [
        sid for sid in script.voice_profiles
        if not (_DEFAULT_VOICES_DIR / f"{voice_profile_stem(slug, sid)}.wav").exists()
    ]
    if missing_speakers:
        raise FileNotFoundError(
            f"Missing dialogue voice samples for speakers {missing_speakers}. "
            "Run Stage 2 (create_ereader) without --skip-audio first."
        )

    base_dir = Path(config.book_dir("podcasts", slug))
    output_dir = Path(config.audiobook_output_dir("podcasts"))
    output_path = output_dir / f"{slug}.mp3"

    log.info("event=podcast_mp3_start episode=%s", slug)

    t0 = time.time()
    if service_manager:
        service_manager.stop_if_running("llm")
        service_manager.start_if_needed("tts")
    try:
        service = Qwen3TTSService()
        result = assemble_podcast_mp3(
            script,
            slug,
            service,
            base_dir,
            output_path,
            voices_dir=_DEFAULT_VOICES_DIR,
            title=title,
            use_builtin_hosts=use_builtin_hosts,
        )
    finally:
        if service_manager:
            service_manager.stop_if_running("tts")

    log.info(
        "event=podcast_mp3_complete episode=%s path=%s duration_ms=%d",
        slug, result, int((time.time() - t0) * 1000),
    )
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate the full podcast mp3 from a podcast script"
    )
    parser.add_argument(
        "-i", "--input", required=True,
        help="Path to the podcast script file (books_src/podcasts/*.txt)",
    )
    parser.add_argument(
        "--profile", type=str, default=None,
        help="Pipeline profile from pipeline.toml",
    )
    parser.add_argument(
        "--builtin-hosts", action="store_true",
        help="Use built-in Qwen3 voices (Serena/Eric) for hosts instead of voice clone",
    )
    args = parser.parse_args()

    config = PipelineConfig.from_files_and_args(args, profile_name=args.profile)
    service_manager = ServiceManager()

    create_mp3(args.input, config, service_manager=service_manager, use_builtin_hosts=args.builtin_hosts)
