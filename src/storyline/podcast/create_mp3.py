import json
import time
import tomllib
from pathlib import Path

import storyline.logging
from storyline.logging import get_logger
from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.config.pipeline_config import PipelineConfig, CONFIG_DIR
from storyline.podcast.audio_gen import voice_profile_stem
from storyline.podcast.create_ereader import _slug_from_filename, _title_from_slug
from storyline.podcast.mp3_gen import (
    assemble_podcast_mp3,
    HostVoiceConfig,
)
from storyline.podcast.omnivoice_audio import OmnivoiceTTSService
from storyline.podcast.script_parser import parse_script
from storyline.podcast.steps import build_flashcard_intro_sequence
from storyline.services.manager import ServiceManager

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_VOICES_DIR = _PROJECT_ROOT / "voices"


def _load_host_voice_config() -> HostVoiceConfig:
    audio_toml = CONFIG_DIR / "audio.toml"
    if audio_toml.is_file():
        with audio_toml.open("rb") as f:
            raw = tomllib.load(f)
        hosts = raw.get("podcast", {}).get("hosts", {})
        return HostVoiceConfig(
            teacher_builtin_speaker=hosts.get("teacher_builtin_speaker", "Serena"),
            student_builtin_speaker=hosts.get("student_builtin_speaker", "Ryan"),
        )
    return HostVoiceConfig()


def create_mp3(
    script_path: str,
    config: PipelineConfig,
    service_manager: ServiceManager | None = None,
    *,
    use_builtin_hosts: bool = False,
    use_omnivoice: bool = False,
    flashcard_dir: Path | None = None,
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

    # Host voices always use Qwen3TTS; only start the TTS service when hosts
    # will actually be rendered by Qwen3TTS (not by Omnivoice).
    need_tts = use_builtin_hosts or not use_omnivoice
    if service_manager:
        service_manager.stop_if_running("llm")
        if need_tts:
            service_manager.start_if_needed("tts")
        else:
            service_manager.stop_if_running("tts")
    host_service = Qwen3TTSService()

    if use_omnivoice:
        clone_service = OmnivoiceTTSService(
            binary=config.omnivoice_binary,
            model=config.omnivoice_model,
            timeout=config.omnivoice_timeout,
            batch_size=config.omnivoice_batch_size,
            batch_binary=config.omnivoice_batch_binary,
            batch_model=config.omnivoice_batch_model,
        )
    else:
        clone_service = host_service

    t0 = time.time()

    flashcard_intro = None
    flashcard_audio_cache = None
    if flashcard_dir:
        entries_path = flashcard_dir / "flashcard_entries.json"
        if entries_path.is_file():
            with open(entries_path, encoding="utf-8") as f:
                entries = json.load(f)
            flashcard_intro, flashcard_audio_cache = build_flashcard_intro_sequence(
                entries, flashcard_audio_dir=flashcard_dir,
            )
            log.info(
                "event=flashcard_intro_built entries=%d steps=%d cached_audio=%d",
                len(entries), len(flashcard_intro), len(flashcard_audio_cache),
            )

    try:
        vocab_output_path = output_dir / f"{slug}-vocab.mp3" if flashcard_intro else None
        host_voice = _load_host_voice_config()
        result = assemble_podcast_mp3(
            script,
            slug,
            clone_service,
            base_dir,
            output_path,
            host_service=host_service,
            service_manager=service_manager if use_omnivoice else None,
            voices_dir=_DEFAULT_VOICES_DIR,
            host_voice=host_voice,
            title=title,
            use_builtin_hosts=use_builtin_hosts,
            flashcard_intro=flashcard_intro,
            flashcard_audio_cache=flashcard_audio_cache,
            vocab_output_path=vocab_output_path,
        )

        if vocab_output_path:
            log.info("event=flashcard_vocab_mp3 path=%s", vocab_output_path)
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
    parser.add_argument(
        "--omnivoice", action="store_true",
        help="Use omnivoice-infer (CUDA/GPU) for voice cloning instead of Qwen3-TTS",
    )
    args = parser.parse_args()

    config = PipelineConfig.from_files_and_args(args, profile_name=args.profile)
    service_manager = ServiceManager()

    create_mp3(args.input, config, service_manager=service_manager,
               use_builtin_hosts=args.builtin_hosts, use_omnivoice=args.omnivoice)
