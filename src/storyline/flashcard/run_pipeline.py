import argparse
import json
import time
from pathlib import Path

import storyline.logging
from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.config.pipeline_config import PipelineConfig
from storyline.logging import get_logger
from storyline.services.manager import ServiceManager

CACHE_FILE = "flashcard_entries.json"
IMPROMPTS_FILE = "image_prompts.json"

_BUILTIN_FLASHCARD_SPEAKER = "Serena"
_BUILTIN_FLASHCARD_INSTRUCT = ""


def _load_cached_entries(output_dir: Path) -> list[list[str]] | None:
    cache_path = output_dir / CACHE_FILE
    if not cache_path.is_file():
        return None
    with open(cache_path, encoding="utf-8") as f:
        return json.load(f)


def _save_cache(output_dir: Path, entries: list[list[str]]) -> None:
    cache_path = output_dir / CACHE_FILE
    output_dir.mkdir(parents=True, exist_ok=True)
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    get_logger("flashcard.pipeline").info("event=cache_saved path=%s", cache_path)


def _save_image_prompts(output_dir: Path, prompts: list[str]) -> None:
    prompts_path = output_dir / IMPROMPTS_FILE
    with open(prompts_path, "w", encoding="utf-8") as f:
        json.dump(prompts, f, ensure_ascii=False, indent=2)


def _extract_entries(
    script_path: Path,
    prompt_template_path: str,
    service_manager: ServiceManager,
    models: list[str],
    output_dir: Path,
    force: bool,
    *,
    llm_already_running: bool = False,
) -> list[list[str]]:
    if not force:
        cached = _load_cached_entries(output_dir)
        if cached is not None:
            get_logger("flashcard.pipeline").info(
                "event=cache_hit entries=%d", len(cached)
            )
            return cached

    log = get_logger("flashcard.pipeline")
    log.info("event=flashcard_stage stage=1 vocab_extract")

    if not llm_already_running:
        service_manager.start_if_needed("llm")
    try:
        from storyline.flashcard.vocab_extract import extract_vocab

        entries = extract_vocab(
            script_path=script_path,
            prompt_template_path=prompt_template_path,
            service_manager=service_manager,
            models=models,
        )
    finally:
        if not llm_already_running:
            service_manager.stop_if_running("llm")

    _save_cache(output_dir, entries)
    return entries


def generate_flashcard_images_and_pdf(
    output_dir: Path,
    service_manager: ServiceManager,
) -> Path:
    prompts_path = output_dir / IMPROMPTS_FILE
    with open(prompts_path, encoding="utf-8") as f:
        image_prompts = json.load(f)

    from storyline.flashcard.image_gen import generate_images

    generate_images(
        prompts=image_prompts,
        output_dir=output_dir,
        service_manager=service_manager,
    )

    from storyline.flashcard.pdf_gen import generate_pdf

    pdf_path = generate_pdf(input_dir=output_dir)
    return pdf_path


def _write_text_files_and_collect_prompts(
    output_dir: Path, entries: list[list[str]]
) -> list[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    log = get_logger("flashcard.pipeline")
    image_prompts: list[str] = []
    for i, entry in enumerate(entries):
        text_lines = entry[:6]
        image_prompt = entry[6]
        image_prompts.append(image_prompt)

        text_path = output_dir / f"text_{i + 1}.txt"
        if not text_path.is_file():
            text_path.write_text("\n".join(text_lines), encoding="utf-8")
            log.info("event=text_written index=%d path=%s", i + 1, text_path)

    _save_image_prompts(output_dir, image_prompts)
    return image_prompts


def generate_flashcard_audio(
    output_dir: Path,
    entries: list[list[str]],
    service_manager: ServiceManager,
) -> None:
    log = get_logger("flashcard.audio")
    output_dir.mkdir(parents=True, exist_ok=True)

    tts = Qwen3TTSService()
    log.info("event=flashcard_audio_start entries=%d", len(entries))

    for i, entry in enumerate(entries):
        word = entry[0]
        sentence = entry[3]
        idx = i + 1

        word_path = output_dir / f"audio_{idx}_word.mp3"
        sentence_path = output_dir / f"audio_{idx}_sentence.mp3"

        if not word_path.is_file():
            t0 = time.time()
            audio = tts.generate_audio(
                word, "Chinese",
                speaker=_BUILTIN_FLASHCARD_SPEAKER,
                instruct=_BUILTIN_FLASHCARD_INSTRUCT,
            )
            audio.export(str(word_path), format="mp3", bitrate="64k")
            log.info(
                "event=flashcard_audio_word index=%d word=%s duration_ms=%d",
                idx, word, int((time.time() - t0) * 1000),
            )

        if not sentence_path.is_file():
            t0 = time.time()
            audio = tts.generate_audio(
                sentence, "Chinese",
                speaker=_BUILTIN_FLASHCARD_SPEAKER,
                instruct=_BUILTIN_FLASHCARD_INSTRUCT,
            )
            audio.export(str(sentence_path), format="mp3", bitrate="64k")
            log.info(
                "event=flashcard_audio_sentence index=%d chars=%d duration_ms=%d",
                idx, len(sentence), int((time.time() - t0) * 1000),
            )

    log.info("event=flashcard_audio_done entries=%d", len(entries))


def run_flashcard_pipeline(
    episode_name: str,
    script_dir: str = "books_src/podcasts",
    output_base_dir: str = "books/podcasts",
    prompt_template_path: str = "prompts/flashcard-vocab-from-script.md",
    service_manager: ServiceManager | None = None,
    models: list[str] | None = None,
    force: bool = False,
) -> Path:
    log = get_logger("flashcard.pipeline")

    script_path = Path(script_dir) / f"{episode_name}.txt"
    if not script_path.is_file():
        raise FileNotFoundError(f"Script not found: {script_path}")

    output_dir = Path(output_base_dir) / episode_name / "flashcards"
    log.info(
        "event=flashcard_pipeline_start episode=%s script=%s output=%s",
        episode_name, script_path, output_dir,
    )

    if service_manager is None:
        service_manager = ServiceManager()

    if models is None:
        models = ["local-llamacpp"]

    # Stage 1-2: Extract vocab and save text files (skipped on cache hit)
    entries = _extract_entries(
        script_path, prompt_template_path, service_manager, models,
        output_dir, force,
    )
    image_prompts = _write_text_files_and_collect_prompts(output_dir, entries)

    # Stage 3-4: Generate images + PDF
    try:
        pdf_path = generate_flashcard_images_and_pdf(output_dir, service_manager)
    finally:
        service_manager.stop_if_running("image_gen")

    log.info("event=flashcard_pipeline_done pdf=%s", pdf_path)
    return pdf_path


def main():
    parser = argparse.ArgumentParser(
        description="Generate flashcards from a podcast episode script."
    )
    parser.add_argument(
        "episode",
        help="Episode name (e.g. grocery-shopping-for-the-week)",
    )
    parser.add_argument(
        "--script-dir",
        default="books_src/podcasts",
        help="Directory containing podcast scripts (default: books_src/podcasts)",
    )
    parser.add_argument(
        "--output-base-dir",
        default="books/podcasts",
        help="Base directory for podcast output (default: books/podcasts)",
    )
    parser.add_argument(
        "--prompt-template",
        default="prompts/flashcard-vocab-from-script.md",
        help="Path to the vocab extraction prompt template",
    )
    parser.add_argument(
        "-m", "--models",
        type=str,
        default=None,
        help="Comma-separated list of models (overrides config)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-extraction of vocab even if cache exists",
    )
    args = parser.parse_args()

    storyline.logging.init()
    log = get_logger("flashcard.main")

    models = None
    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]

    service_manager = ServiceManager()
    config = PipelineConfig.from_files_and_args(args)

    try:
        pdf_path = run_flashcard_pipeline(
            episode_name=args.episode,
            script_dir=args.script_dir,
            output_base_dir=args.output_base_dir,
            prompt_template_path=args.prompt_template,
            service_manager=service_manager,
            models=models or config.models,
            force=args.force,
        )
        print(f"Flashcards PDF: {pdf_path}")
    finally:
        service_manager.stop("llm")
        service_manager.stop("image_gen")

    log.info("event=flashcard_complete episode=%s", args.episode)


if __name__ == "__main__":
    main()