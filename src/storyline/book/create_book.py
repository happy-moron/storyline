import os
import argparse
import re
import logging
from pathlib import Path

from storyline.book.parse_pipe_format import (
    parse_source_file,
    parse_tokenized_file,
)
from storyline.book.parse_chunk_format import (
    parse_chunk_format,
    save_chunks_json,
)
from storyline.book.cjk_punct import strip as strip_cjk_punct, reinsert as reinsert_cjk_punct, format_compact
from storyline.book.split_text import split_text
from storyline.prompt_utils.run_prompt import run_prompt
from storyline.book.create_custom_dict import process_json_file, load_dictionary
from storyline.services.manager import ServiceManager
from storyline.audio.audiobook_gen_qwen3 import process_json_to_audio
from storyline.audio.audiobook_gen_chunk import process_chapter_chunks
from storyline.book.tokenization_repair import (
    validate_full,
    build_repair_input,
    apply_repairs,
)
from storyline.book.update_manifest import update_manifest
from storyline.config.pipeline_config import PipelineConfig
from zsp_llm_client.prompt_runner import PromptRunner


def _resolve_profile(task_profiles: dict, task: str) -> str | None:
    if not task_profiles:
        return None
    default = task_profiles.get("default") or task_profiles.get("translate")
    return task_profiles.get(task, default)


def create_book(input_text: str, author: str, config: PipelineConfig,
                service_manager: ServiceManager | None = None):

    logging.basicConfig(format='%(asctime)s %(message)s', datefmt='%H:%M:%S', level=logging.INFO)
    log = logging.getLogger(__name__)

    book = os.path.splitext(os.path.basename(input_text))[0]
    llm_local = config.llm_provider == "local"

    base_dir = config.book_dir(author, book)
    split_source_dir = os.path.join(base_dir, "split", "source")
    pipe_source_dir = os.path.join(base_dir, "pipe", "source")
    pipe_token_dir = os.path.join(base_dir, "pipe", "tokenized")
    audio_dir = os.path.join(base_dir, "audio")
    audiobook_dir = config.audiobook_output_dir(book)

    # -- Warmup (local LLM only) --
    if service_manager and llm_local and config.warmup_on_start:
        warmup_profile = _resolve_profile(config.task_profiles, 'translate')
        service_manager.ensure_llm_profile(warmup_profile)
        log.info("Warming up LLM (first call compiles CUDA graphs, may take a while)...")
        warmup_runner = PromptRunner()
        warmup_runner.run(
            config.resolve_prompt("warmup"),
            "Hello.",
            models=config.models,
        )
        log.info("LLM warmup complete.")

    # -- 1. Split --
    split_text(input_text, split_source_dir, chunk_size=config.split_chunk_size)

    input_files = list(Path(split_source_dir).glob('*.txt'))
    ordered_files = natural_sort_filenames(input_files)

    dictionary = load_dictionary(config.dict_file)
    processed = 0

    for source_file in ordered_files:
        processed += 1
        if processed > config.max_chunks:
            break

        source_file_stem = os.path.splitext(os.path.basename(source_file))[0]
        log.info("processing %s", source_file_stem)

        # -- 2. Simplify (optional) --
        simple_dir = os.path.join(base_dir, "split", "simple")
        os.makedirs(simple_dir, exist_ok=True)
        simple_file_path = Path(simple_dir) / Path(source_file_stem + ".txt")

        if not config.skip_simplify and not simple_file_path.exists():
            if service_manager and llm_local:
                simplify_profile = _resolve_profile(config.task_profiles, 'translate')
                service_manager.ensure_llm_profile(simplify_profile)

            for attempt in range(config.llm_retries):
                run_prompt(config.resolve_prompt("simplify"),
                           source_file, simple_file_path,
                           models=config.models, timeout=config.llm_timeout_s)
                simple_text = Path(simple_file_path).read_text(encoding="utf-8").strip()
                if simple_text:
                    break
                if attempt < config.llm_retries - 1:
                    log.info("  Simplify produced empty output (attempt %d/%d), retrying...",
                             attempt + 1, config.llm_retries)
                    os.remove(simple_file_path)
                else:
                    raise ValueError("Simplify produced empty output after all retries")
            log.info("  -> wrote simplified text to %s", simple_file_path)

        # -- 2.5 Chunk --
        chunks_dir = os.path.join(base_dir, "chunks")
        os.makedirs(chunks_dir, exist_ok=True)
        chunk_json_path = Path(chunks_dir) / Path(source_file_stem + ".json")
        chunk_raw_path = Path(chunks_dir) / Path(source_file_stem + "_raw.txt")

        if not chunk_json_path.exists():
            chunk_input = simple_file_path if (not config.skip_simplify and simple_file_path.exists()) else source_file

            if service_manager and llm_local:
                chunk_profile = _resolve_profile(config.task_profiles, 'translate')
                service_manager.ensure_llm_profile(chunk_profile)

            for attempt in range(config.llm_retries):
                run_prompt(config.resolve_prompt("chunk"),
                           chunk_input, chunk_raw_path,
                           models=config.models, timeout=config.llm_timeout_s)
                raw_text = chunk_raw_path.read_text(encoding="utf-8").strip()
                if raw_text:
                    break
                if attempt < config.llm_retries - 1:
                    log.info("  Chunking produced empty output (attempt %d/%d), retrying...",
                             attempt + 1, config.llm_retries)
                    os.remove(chunk_raw_path)
                else:
                    raise ValueError("Chunking produced empty output after all retries")

            source_lines = [l.strip() for l in Path(chunk_input).read_text(encoding="utf-8").strip().split("\n") if l.strip()]
            chunks = parse_chunk_format(raw_text, source_lines)
            save_chunks_json(chunks, chunk_json_path)
            log.info("  -> wrote %d chunks to %s", len(chunks), chunk_json_path)

        # -- 3. Translate --
        os.makedirs(pipe_source_dir, exist_ok=True)
        source_txt_path = Path(pipe_source_dir) / Path(source_file_stem + ".txt")
        if not source_txt_path.exists():
            if service_manager and llm_local:
                translate_profile = _resolve_profile(config.task_profiles, 'translate')
                service_manager.ensure_llm_profile(translate_profile)

            translate_input = simple_file_path if (not config.skip_simplify and simple_file_path.exists()) else source_file

            for attempt in range(config.llm_retries):
                run_prompt(config.resolve_prompt("translate"),
                           translate_input, source_txt_path,
                           models=config.models, timeout=config.llm_timeout_s)
                chinese_lines = Path(source_txt_path).read_text(encoding="utf-8").strip().split("\n")
                english_lines = [
                    l for l in Path(translate_input).read_text(encoding="utf-8").strip().split("\n")
                    if l.strip()
                ]
                pipe_lines = []
                for i, ch in enumerate(chinese_lines):
                    ch = ch.strip()
                    if not ch:
                        continue
                    en = english_lines[i] if i < len(english_lines) else ""
                    pipe_lines.append(f"{ch}|||{en.strip()}")
                pipe_text = "\n".join(pipe_lines) + "\n"
                Path(source_txt_path).write_text(pipe_text, encoding="utf-8")
                try:
                    sentences = parse_source_file(source_txt_path)
                    break
                except ValueError:
                    if attempt < config.llm_retries - 1:
                        log.info("  Translation parse failed (attempt %d/%d), retrying...",
                                 attempt + 1, config.llm_retries)
                        os.remove(source_txt_path)
                    else:
                        raise
            log.info("  -> wrote %d sentence pairs to %s", len(sentences), source_txt_path)

        # -- 4. Tokenize --
        os.makedirs(pipe_token_dir, exist_ok=True)
        token_txt_path = Path(pipe_token_dir) / Path(source_file_stem + ".txt")
        if not token_txt_path.exists():
            source_sentences = parse_source_file(source_txt_path)
            chinese_sentences = [
                "".join(item["chinese"].split()) for item in source_sentences
            ]

            stripped_sentences = []
            punct_maps = []
            for ch in chinese_sentences:
                stripped, positions = strip_cjk_punct(ch)
                stripped_sentences.append(stripped)
                punct_maps.append(positions)

            chinese_input_path = Path(pipe_token_dir) / Path(source_file_stem + "_input.txt")
            with open(chinese_input_path, "w", encoding="utf-8") as f:
                f.write("\n".join(stripped_sentences))

            if service_manager and llm_local:
                tokenize_profile = _resolve_profile(config.task_profiles, 'tokenize')
                service_manager.ensure_llm_profile(tokenize_profile)

            for attempt in range(config.llm_retries):
                run_prompt(config.resolve_prompt("tokenize"),
                           chinese_input_path, token_txt_path,
                           models=config.models, timeout=config.llm_timeout_s)

                raw_text = Path(token_txt_path).read_text(encoding="utf-8")
                try:
                    tokenized = parse_tokenized_file(token_txt_path, expected_chinese=stripped_sentences)
                    break
                except ValueError:
                    if attempt >= config.llm_retries - 1:
                        raise

                    try:
                        tokenized_no_val = parse_tokenized_file(token_txt_path)
                        raw_lines = raw_text.strip().split("\n")
                        report = validate_full(stripped_sentences, tokenized_no_val, raw_lines)
                    except ValueError:
                        log.info("  Tokenization unparseable (attempt %d/%d), retrying...",
                                 attempt + 1, config.llm_retries)
                        os.remove(token_txt_path)
                        continue

                    if report.is_clean:
                        tokenized = tokenized_no_val
                        break

                    if report.has_structural_errors:
                        log.info("  Tokenization: structural errors, full retry (attempt %d/%d)",
                                 attempt + 1, config.llm_retries)
                        os.remove(token_txt_path)
                        continue

                    log.info("  Tokenization: %d/%d errors, attempting repair...",
                             len(report.bad_indices), len(stripped_sentences))
                    repair_input = build_repair_input(report, stripped_sentences, raw_lines)
                    repair_input_path = Path(pipe_token_dir) / Path(source_file_stem + "_repair_input.txt")
                    repair_output_path = Path(pipe_token_dir) / Path(source_file_stem + "_repair_output.txt")

                    with open(repair_input_path, "w", encoding="utf-8") as f:
                        f.write(repair_input)

                    run_prompt(config.resolve_prompt("fix_tokenization"),
                               repair_input_path, repair_output_path,
                               models=config.models, timeout=config.llm_timeout_s)

                    repaired_text = Path(repair_output_path).read_text(encoding="utf-8")
                    repaired_lines = apply_repairs(raw_lines, repaired_text, report.bad_indices)

                    with open(token_txt_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(repaired_lines) + "\n")

                    os.remove(repair_input_path)
                    os.remove(repair_output_path)

                    try:
                        tokenized = parse_tokenized_file(token_txt_path, expected_chinese=stripped_sentences)
                        log.info("  Repair successful!")
                        break
                    except ValueError:
                        log.info("  Repair failed, full retry (attempt %d/%d)",
                                 attempt + 1, config.llm_retries)
                        os.remove(token_txt_path)
                        continue

            os.remove(chinese_input_path)

            for i, sentence_data in enumerate(tokenized):
                original = chinese_sentences[i]
                sentence_data["t"] = reinsert_cjk_punct(
                    sentence_data["t"], punct_maps[i], original
                )

            with open(token_txt_path, "w", encoding="utf-8") as f:
                for sentence_data in tokenized:
                    f.write(format_compact(sentence_data["t"]) + "\n")

            log.info("  -> wrote %d tokenized sentences to %s", len(tokenized), token_txt_path)

        # -- 5. Audio --
        if not config.skip_audio:
            audiobook_mp3_path = Path(audiobook_dir) / Path(source_file_stem + ".mp3")
            os.makedirs(audio_dir, exist_ok=True)
            os.makedirs(audiobook_dir, exist_ok=True)
            os.makedirs(chunks_dir, exist_ok=True)

            if not audiobook_mp3_path.exists():
                if service_manager:
                    service_manager.start_if_needed('tts')

                if chunk_json_path.exists():
                    # Chunk-based flow (Step 4)
                    english_input = simple_file_path if (not config.skip_simplify and simple_file_path.exists()) else source_file
                    process_chapter_chunks(
                        source_txt_path,
                        english_input,
                        chunk_json_path,
                        chapters_audio_dir=chunks_dir,
                        aggregate_output_path=audiobook_mp3_path,
                        profile_key=config.audio_profile,
                    )
                else:
                    # Fallback: per-sentence flow
                    process_json_to_audio(
                        source_txt_path,
                        os.path.join(audiobook_dir, source_file_stem + ".mp3"),
                        standalone_file=os.path.join(audio_dir, source_file_stem),
                        profile_key=config.audio_profile,
                    )

                if service_manager:
                    service_manager.stop_if_running('tts')
                    if llm_local:
                        dict_profile = _resolve_profile(config.task_profiles, 'dictionary')
                        service_manager.ensure_llm_profile(dict_profile)

        # -- 6. Dictionary --
        if service_manager and llm_local:
            dict_profile = _resolve_profile(config.task_profiles, 'dictionary')
            service_manager.ensure_llm_profile(dict_profile)

        process_json_file(token_txt_path, dictionary, config.dict_file,
                          config.resolve_prompt("dict_entry"),
                          models=config.models,
                          punctuation_skip=config.punctuation_skip)

    update_manifest(config.books_dir)

    if service_manager and llm_local:
        service_manager.stop('llm')


def natural_sort_filenames(filenames):
    def extract_number(filename):
        match = re.search(r'_(\d+)\.', str(filename))
        if match:
            return int(match.group(1))
        return 0

    return sorted(filenames, key=extract_number)


if __name__ == '__main__':
    service_manager = ServiceManager()

    parser = argparse.ArgumentParser(description="Generate an audio book")

    parser.add_argument(
        "-m", "--models", type=str, default=None,
        help="Comma-separated list of models (overrides config file)",
    )
    parser.add_argument('-a', '--author', required=True, help='author name')
    parser.add_argument('-i', '--input', required=True, help='plaintext input')
    parser.add_argument(
        '--profile', type=str, default=None,
        help="Pipeline profile from pipeline.toml (e.g. quick, test, voice-clone)",
    )
    parser.add_argument(
        '--skip-simplify', default=None, action=argparse.BooleanOptionalAction,
        dest='skip_simplify',
    )
    parser.add_argument(
        '--max-chunks', dest='max_chunks', type=int, default=None,
        help='max number of chunks',
    )
    parser.add_argument(
        '--skip-audio', dest='skip_audio', default=None,
        action=argparse.BooleanOptionalAction, help='Skip audio generation',
    )
    parser.add_argument(
        '--audio-profile', default=None,
        help='Audio profile from audio.toml (e.g. default, voice-clone)',
    )
    args = parser.parse_args()

    config = PipelineConfig.from_files_and_args(args, profile_name=args.profile)

    try:
        create_book(
            args.input,
            args.author,
            config,
            service_manager=service_manager,
        )
    finally:
        if config.llm_provider == "local":
            service_manager.stop('llm')