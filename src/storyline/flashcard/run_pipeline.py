import argparse
import json
import shutil
import time
from pathlib import Path

import storyline.logging
from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.config.pipeline_config import PipelineConfig
from storyline.flashcard.vocab_extract import extract_vocab, extract_vocab_from_list
from storyline.logging import get_logger
from storyline.services.manager import ServiceManager

CACHE_FILE = "flashcard_entries.json"
IMPROMPTS_FILE = "image_prompts.json"
MANIFEST_FILE = "flashcard-manifest.json"

_FLASHCARD_BUILTIN_SPEAKER = "Serena"
_FLASHCARD_BUILTIN_INSTRUCT = ""

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_VOICES_DIR = _PROJECT_ROOT / "voices"

_VOCAB_DIR = _PROJECT_ROOT / "books" / "vocab"
_MANIFEST_PATH = _PROJECT_ROOT / "books" / MANIFEST_FILE


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
    service_manager: ServiceManager | None = None,
    *,
    use_omnivoice: bool = False,
    use_builtin_hosts: bool = False,
    voices_dir: Path | None = None,
) -> None:
    log = get_logger("flashcard.audio")
    output_dir.mkdir(parents=True, exist_ok=True)

    if voices_dir is None:
        voices_dir = _DEFAULT_VOICES_DIR

    tts = Qwen3TTSService()

    if use_builtin_hosts:
        clone_service = tts
    else:
        if use_omnivoice:
            from storyline.podcast.omnivoice_audio import OmnivoiceTTSService

            clone_service = OmnivoiceTTSService()
        else:
            clone_service = tts

    ref_audio = str(voices_dir / "teacher.wav")
    ref_text = (voices_dir / "teacher-ref.txt").read_text(encoding="utf-8").strip()

    log.info(
        "event=flashcard_audio_start entries=%d builtin=%s omnivoice=%s",
        len(entries), use_builtin_hosts, use_omnivoice,
    )

    for i, entry in enumerate(entries):
        word = entry[0]
        sentence = entry[3]
        idx = i + 1

        word_path = output_dir / f"audio_{idx}_word.mp3"
        sentence_path = output_dir / f"audio_{idx}_sentence.mp3"

        if not word_path.is_file():
            t0 = time.time()
            if use_builtin_hosts:
                audio = tts.generate_audio(
                    word, "Chinese",
                    speaker=_FLASHCARD_BUILTIN_SPEAKER,
                    instruct=_FLASHCARD_BUILTIN_INSTRUCT,
                )
            else:
                audio = clone_service.generate_voice_clone(
                    word, "Chinese", ref_audio, ref_text,
                )
            audio.export(str(word_path), format="mp3", bitrate="64k")
            log.info(
                "event=flashcard_audio_word index=%d word=%s duration_ms=%d",
                idx, word, int((time.time() - t0) * 1000),
            )

        if not sentence_path.is_file():
            t0 = time.time()
            if use_builtin_hosts:
                audio = tts.generate_audio(
                    sentence, "Chinese",
                    speaker=_FLASHCARD_BUILTIN_SPEAKER,
                    instruct=_FLASHCARD_BUILTIN_INSTRUCT,
                )
            else:
                audio = clone_service.generate_voice_clone(
                    sentence, "Chinese", ref_audio, ref_text,
                )
            audio.export(str(sentence_path), format="mp3", bitrate="64k")
            log.info(
                "event=flashcard_audio_sentence index=%d chars=%d duration_ms=%d",
                idx, len(sentence), int((time.time() - t0) * 1000),
            )

    log.info("event=flashcard_audio_done entries=%d", len(entries))


def _load_flashcard_manifest() -> dict:
    if _MANIFEST_PATH.is_file():
        with open(_MANIFEST_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"version": 1, "words": {}, "books": {}}


def _save_flashcard_manifest(manifest: dict) -> None:
    _MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(_MANIFEST_PATH, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


def _update_manifest_with_entries(
    manifest: dict, episode: str, entries: list[list[str]]
) -> None:
    log = get_logger("flashcard.pipeline")
    manifest["books"][episode] = []
    for entry in entries:
        word = entry[0]
        manifest["books"][episode].append(word)
        if word not in manifest["words"]:
            manifest["words"][word] = {
                "pinyin": entry[1],
                "definition": entry[2],
                "sentence_cn": entry[3],
                "sentence_py": entry[4],
                "sentence_en": entry[5],
                "image_prompt": entry[6],
                "canonical_source": episode,
                "sources": [episode],
            }
            log.info("event=manifest_new_word word=%s episode=%s", word, episode)
        else:
            if episode not in manifest["words"][word]["sources"]:
                manifest["words"][word]["sources"].append(episode)
                log.info(
                    "event=manifest_existing_word word=%s episode=%s sources=%d",
                    word, episode, len(manifest["words"][word]["sources"]),
                )


_VOCAB_ASSET_MAP = [
    ("audio",   "audio_{}_word.mp3",       "{}_word.mp3"),
    ("audio",   "audio_{}_sentence.mp3",    "{}_sentence.mp3"),
    ("images",  "img_{}.png",              "{}.png"),
    ("images",  "img_{}_inverted.png",      "{}_inverted.png"),
    ("texts",   "text_{}.txt",             "{}.txt"),
]


def _copy_canonical_assets(
    output_dir: Path, manifest: dict, episode: str
) -> None:
    log = get_logger("flashcard.pipeline")

    for idx, word in enumerate(manifest["books"].get(episode, []), start=1):
        wd = manifest["words"].get(word)
        if wd and wd["canonical_source"] != episode:
            continue

        for subdir, src_tpl, dst_tpl in _VOCAB_ASSET_MAP:
            dest_sub = _VOCAB_DIR / subdir
            src = output_dir / src_tpl.format(idx)
            dst = dest_sub / dst_tpl.format(word)
            if src.is_file() and not dst.is_file():
                dest_sub.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(dst))

    log.info(
        "event=vocab_assets_copied episode=%s canonical_count=%d",
        episode, sum(
            1 for w in manifest["books"].get(episode, [])
            if manifest["words"].get(w, {}).get("canonical_source") == episode
        ),
    )


def _write_slim_entries(output_dir: Path, entries: list[list[str]]) -> None:
    word_list = [e[0] for e in entries]
    cache_path = output_dir / CACHE_FILE
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump(word_list, f, ensure_ascii=False, indent=2)
    get_logger("flashcard.pipeline").info(
        "event=slim_entries_written path=%s words=%d", cache_path, len(word_list)
    )


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
    _write_text_files_and_collect_prompts(output_dir, entries)

    # Update shared flashcard manifest
    manifest = _load_flashcard_manifest()
    _update_manifest_with_entries(manifest, episode_name, entries)
    _save_flashcard_manifest(manifest)

    # Stage 3-4: Generate images + PDF
    try:
        pdf_path = generate_flashcard_images_and_pdf(output_dir, service_manager)
    finally:
        service_manager.stop_if_running("image_gen")

    # Copy canonical assets to shared vocab store
    _copy_canonical_assets(output_dir, manifest, episode_name)

    log.info("event=flashcard_pipeline_done pdf=%s", pdf_path)
    return pdf_path


def run_flashcard_pipeline_from_list(
    word_list_path: str,
    vocab_base_dir: str = "books/vocab",
    prompt_template_path: str = "prompts/flashcard-vocab-from-list.md",
    service_manager: ServiceManager | None = None,
    models: list[str] | None = None,
    force: bool = False,
    start_batch: int | None = None,
) -> list[Path]:
    """Generate flashcards from a word list file (one word per line).

    Words are grouped into batches of 6. Each batch is saved under
    books/vocab/manual_N/ and registered in the flashcard manifest.

    Returns list of paths to generated PDFs.
    """
    log = get_logger("flashcard.pipeline")
    word_list_path = Path(word_list_path)

    if not word_list_path.is_file():
        raise FileNotFoundError(f"Word list not found: {word_list_path}")

    words = [
        line.strip() for line in word_list_path.read_text(encoding="utf-8").split("\n")
        if line.strip()
    ]
    if not words:
        raise ValueError(f"Word list is empty: {word_list_path}")

    log.info(
        "event=flashcard_list_start path=%s total_words=%d",
        word_list_path, len(words),
    )

    if service_manager is None:
        service_manager = ServiceManager()

    if models is None:
        models = ["local-llamacpp"]

    # Determine starting batch number
    manifest = _load_flashcard_manifest()
    if start_batch is not None:
        batch_num = start_batch
    else:
        batch_num = 1
        existing = [k for k in manifest.get("books", {}) if k.startswith("manual_")]
        if existing:
            nums = []
            for k in existing:
                try:
                    nums.append(int(k.split("_")[-1]))
                except (ValueError, IndexError):
                    pass
            if nums:
                batch_num = max(nums) + 1

    # Group words into batches of 6
    batch_size = 6
    batches = [words[i : i + batch_size] for i in range(0, len(words), batch_size)]

    log.info(
        "event=flashcard_batches batches=%d start_batch=%d",
        len(batches), batch_num,
    )

    vocab_base = Path(vocab_base_dir)
    pdf_paths: list[Path] = []

    for batch_words in batches:
        batch_key = f"manual_{batch_num}"
        output_dir = vocab_base / batch_key
        output_dir.mkdir(parents=True, exist_ok=True)

        log.info(
            "event=flashcard_batch_start batch=%s words=%d output=%s",
            batch_key, len(batch_words), output_dir,
        )

        # Stage 1: Extract entries via LLM
        if force or not _load_cached_entries(output_dir):
            log.info(
                "event=flashcard_list_stage1 batch=%s", batch_key,
            )
            try:
                service_manager.start_if_needed("llm")
                entries = extract_vocab_from_list(
                    batch_words,
                    prompt_template_path,
                    service_manager,
                    models,
                )
            finally:
                service_manager.stop_if_running("llm")
            _save_cache(output_dir, entries)
        else:
            entries = _load_cached_entries(output_dir)
            log.info(
                "event=flashcard_list_cache_hit batch=%s entries=%d",
                batch_key, len(entries),
            )

        # Pad partial batch to 6 with empty stubs so image/PDF generation
        # always sees a full set of cards.  The manifest and slim cache
        # use the original (unpadded) entries to avoid pollution.
        padded_entries = list(entries)
        while len(padded_entries) < batch_size:
            padded_entries.append(["", "", "", "", "", "", "white square"])

        if len(padded_entries) != batch_size:
            raise RuntimeError(
                f"Batch padding failed: {len(padded_entries)} != {batch_size}"
            )

        # Stage 2: Write text files + collect image prompts (from padded list)
        _write_text_files_and_collect_prompts(output_dir, padded_entries)

        # Stage 3: Update shared flashcard manifest (from original list)
        manifest = _load_flashcard_manifest()
        _update_manifest_with_entries(manifest, batch_key, entries)
        _save_flashcard_manifest(manifest)

        # Stage 4: Generate images + PDF (padded to 6, so always ready)
        try:
            pdf_path = generate_flashcard_images_and_pdf(
                output_dir, service_manager
            )
            pdf_paths.append(pdf_path)
            log.info(
                "event=flashcard_batch_pdf batch=%s pdf=%s cards=%d",
                batch_key, pdf_path, len(entries),
            )
        finally:
            service_manager.stop_if_running("image_gen")

        # Stage 5: Copy canonical assets to shared vocab store
        _copy_canonical_assets(output_dir, manifest, batch_key)

        # Write slim cache for reference (original entries only)
        _write_slim_entries(output_dir, entries)

        batch_num += 1

    log.info(
        "event=flashcard_list_done batches=%d pdfs=%d",
        len(batches), len(pdf_paths),
    )
    return pdf_paths


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