import os
import argparse
import re
import time
from pathlib import Path

import storyline.logging
from storyline.logging import get_logger
from storyline.book.parse_pipe_format import (
    parse_source_file,
    parse_tokenized_file,
)
from storyline.book.parse_chunk_format import (
    parse_chunk_format,
    save_chunks_json,
    load_chunks_json,
)
from storyline.book.cjk_punct import strip as strip_cjk_punct, reinsert as reinsert_cjk_punct, format_compact
from storyline.book.split_text import split_text
from storyline.prompt_utils.run_prompt import run_prompt, run_prompt_with_metrics
from storyline.book.create_custom_dict import process_json_file, load_dictionary
from storyline.services.manager import ServiceManager
from storyline.audio.audiobook_gen_qwen3 import process_json_to_audio
from storyline.audio.audiobook_gen_chunk import process_chapter_chunks
from storyline.book.tokenization_repair import (
    ErrorType,
    TokenError,
    ValidationReport,
    validate_full,
    apply_fixes,
)
from storyline.book.update_manifest import update_manifest
from storyline.config.pipeline_config import PipelineConfig
from zsp_llm_client.prompt_runner import PromptRunner


def _resolve_extra_options(config: PipelineConfig, task: str) -> dict | None:
    budget = config.resolve_thinking_budget(task)
    if budget is not None and budget > 0:
        return {"thinking_budget_tokens": budget}
    return None


def _resolve_profile(task_profiles: dict, task: str) -> str | None:
    if not task_profiles:
        return None
    default = task_profiles.get("default") or task_profiles.get("translate")
    return task_profiles.get(task, default)


def _log_llm_call(log, prompt: str, chapter: str, metrics: dict, chars_in=None, attempt=None):
    extra = {}
    if chars_in is not None:
        extra["chars_in"] = chars_in
    if attempt is not None:
        extra["attempt"] = attempt
    for k in ("prompt_tokens", "eval_tokens", "prompt_tps", "eval_tps",
              "server_total_ms", "cache", "model_hint"):
        if k in metrics:
            extra[k] = metrics[k]
    parts = [f"event=llm_call prompt={prompt} chapter={chapter} wall_ms={metrics['wall_ms']}"]
    parts += [f"{k}={v}" for k, v in extra.items()]
    log.info(" ".join(parts))


def create_book(input_text: str, author: str, config: PipelineConfig,
                service_manager: ServiceManager | None = None):

    book = os.path.splitext(os.path.basename(input_text))[0]
    storyline.logging.init()
    log = get_logger("book")

    t_pipeline_start = time.time()
    llm_local = config.llm_provider == "local"

    base_dir = config.book_dir(author, book)
    split_source_dir = os.path.join(base_dir, "split", "source")
    pipe_source_dir = os.path.join(base_dir, "pipe", "source")
    pipe_token_dir = os.path.join(base_dir, "pipe", "tokenized")
    audio_dir = os.path.join(base_dir, "audio")
    audiobook_dir = config.audiobook_output_dir(book)

    log.info("event=pipeline_start book=%s author=%s profile=%s",
             book, author, config.profile_name or "default")

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
    t_split = time.time()
    split_text(input_text, split_source_dir, chunk_size=config.split_chunk_size)
    log.info("event=pipeline_step step=split duration_ms=%d", int((time.time() - t_split) * 1000))

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

        t_simplify = time.time()
        if not config.skip_simplify and not simple_file_path.exists():
            if service_manager and llm_local:
                simplify_profile = _resolve_profile(config.task_profiles, 'translate')
                service_manager.ensure_llm_profile(simplify_profile)

            simplify_chars_in = len(Path(source_file).read_text(encoding="utf-8").strip())
            for attempt in range(config.llm_retries):
                llm_metrics = run_prompt_with_metrics(
                    config.resolve_prompt("simplify"),
                    source_file, simple_file_path,
                    models=config.models, timeout=config.llm_timeout_s,
                    prompt_label="simplify",
                    extra_options=_resolve_extra_options(config, "translate"),
                )
                _log_llm_call(log, "simplify", source_file_stem, llm_metrics,
                               chars_in=simplify_chars_in, attempt=attempt + 1)
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

            # Remove empty/whitespace lines from simplified output
            slines = simple_file_path.read_text(encoding="utf-8").strip().split("\n")
            clean = [l for l in slines if l.strip()]
            if len(clean) != len(slines):
                simple_file_path.write_text("\n".join(clean) + "\n", encoding="utf-8")
                log.info("  -> removed %d empty lines", len(slines) - len(clean))

            simple_result = simple_file_path.read_text(encoding="utf-8").strip()
            source_raw = source_file.read_text(encoding="utf-8").strip()
            log.info("event=pipeline_step step=simplify chapter=%s chars_in=%d chars_out=%d duration_ms=%d attempt=%d",
                     source_file_stem, len(source_raw), len(simple_result),
                     int((time.time() - t_simplify) * 1000), attempt + 1)

        # -- 2.5 Chunk --
        chunks_dir = os.path.join(base_dir, "chunks")
        os.makedirs(chunks_dir, exist_ok=True)
        chunk_json_path = Path(chunks_dir) / Path(source_file_stem + ".json")
        chunk_raw_path = Path(chunks_dir) / Path(source_file_stem + "_raw.txt")

        t_chunk = time.time()
        if not chunk_json_path.exists():
            chunk_input = simple_file_path if (not config.skip_simplify and simple_file_path.exists()) else source_file

            if service_manager and llm_local:
                chunk_profile = _resolve_profile(config.task_profiles, 'translate')
                service_manager.ensure_llm_profile(chunk_profile)

            # Build numbered input so the LLM can reference line ranges
            clines = Path(chunk_input).read_text(encoding="utf-8").strip().split("\n")
            nonempty = [l.strip() for l in clines if l.strip()]
            numbered = [f"{i+1}: {line}" for i, line in enumerate(nonempty)]
            chunk_numbered_path = Path(chunks_dir) / Path(source_file_stem + "_numbered.txt")
            chunk_numbered_path.write_text("\n".join(numbered) + "\n", encoding="utf-8")

            chunk_chars_in = len(Path(chunk_input).read_text(encoding="utf-8").strip())
            for attempt in range(config.llm_retries):
                llm_metrics = run_prompt_with_metrics(
                    config.resolve_prompt("chunk"),
                    chunk_numbered_path, chunk_raw_path,
                    models=config.models, timeout=config.llm_timeout_s,
                    prompt_label="chunk",
                    extra_options=_resolve_extra_options(config, "translate"),
                )
                _log_llm_call(log, "chunk", source_file_stem, llm_metrics,
                               chars_in=chunk_chars_in, attempt=attempt + 1)
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
            log.info("event=pipeline_step step=chunk chapter=%s lines=%d nchunks=%d duration_ms=%d",
                     source_file_stem, len(nonempty), len(chunks),
                     int((time.time() - t_chunk) * 1000))

        # -- 3. Translate --
        os.makedirs(pipe_source_dir, exist_ok=True)
        source_txt_path = Path(pipe_source_dir) / Path(source_file_stem + ".txt")
        t_translate = time.time()
        if not source_txt_path.exists():
            if service_manager and llm_local:
                translate_profile = _resolve_profile(config.task_profiles, 'translate')
                service_manager.ensure_llm_profile(translate_profile)

            translate_input = simple_file_path if (not config.skip_simplify and simple_file_path.exists()) else source_file

            translate_chars_in = len(Path(translate_input).read_text(encoding="utf-8").strip())
            for attempt in range(config.llm_retries):
                llm_metrics = run_prompt_with_metrics(
                    config.resolve_prompt("translate"),
                    translate_input, source_txt_path,
                    models=config.models, timeout=config.llm_timeout_s,
                    prompt_label="translate",
                    extra_options=_resolve_extra_options(config, "translate"),
                )
                _log_llm_call(log, "translate", source_file_stem, llm_metrics,
                               chars_in=translate_chars_in, attempt=attempt + 1)
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
            log.info("event=pipeline_step step=translate chapter=%s pairs=%d duration_ms=%d attempt=%d",
                     source_file_stem, len(sentences),
                     int((time.time() - t_translate) * 1000), attempt + 1)

        # -- 4. Tokenize --
        os.makedirs(pipe_token_dir, exist_ok=True)
        token_txt_path = Path(pipe_token_dir) / Path(source_file_stem + ".txt")
        t_tokenize = time.time()
        tokenize_retries = 0
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

            MAX_TOKENIZE_ATTEMPTS = 3
            for attempt in range(MAX_TOKENIZE_ATTEMPTS):
                tokenize_retries = attempt
                if attempt == 0:
                    # Full tokenization of all sentences
                    tok_chars_in = len("\n".join(stripped_sentences))
                    llm_metrics = run_prompt_with_metrics(
                        config.resolve_prompt("tokenize"),
                        chinese_input_path, token_txt_path,
                        models=config.models, timeout=config.llm_timeout_s,
                        prompt_label="tokenize",
                        extra_options=_resolve_extra_options(config, "tokenize"),
                    )
                    _log_llm_call(log, "tokenize", source_file_stem, llm_metrics,
                                   chars_in=tok_chars_in, attempt=attempt + 1)
                else:
                    # Fix only the remaining failing sentences
                    target_indices = sorted(report.bad_indices)
                    bad_sentences = [stripped_sentences[i] for i in target_indices]

                    fix_input_path = Path(pipe_token_dir) / Path(
                        source_file_stem + f"_fix_input_{attempt}.txt")
                    fix_output_path = Path(pipe_token_dir) / Path(
                        source_file_stem + f"_fix_output_{attempt}.txt")

                    with open(fix_input_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(bad_sentences))

                    fix_chars_in = len("\n".join(bad_sentences))
                    llm_metrics = run_prompt_with_metrics(
                        config.resolve_prompt("fix_tokenization"),
                        fix_input_path, fix_output_path,
                        models=config.models, timeout=config.llm_timeout_s,
                        prompt_label="fix_tokenization",
                        extra_options=_resolve_extra_options(config, "tokenize"),
                    )
                    _log_llm_call(log, "fix_tokenization", source_file_stem, llm_metrics,
                                   chars_in=fix_chars_in, attempt=attempt + 1)

                    fix_output_text = Path(fix_output_path).read_text(encoding="utf-8")
                    raw_lines = apply_fixes(raw_lines, fix_output_text, target_indices)

                    with open(token_txt_path, "w", encoding="utf-8") as f:
                        f.write("\n".join(raw_lines) + "\n")

                    os.remove(fix_input_path)
                    os.remove(fix_output_path)

                raw_text = Path(token_txt_path).read_text(encoding="utf-8")

                try:
                    tokenized = parse_tokenized_file(token_txt_path, expected_chinese=stripped_sentences)
                    break
                except ValueError:
                    # Parse or content validation failed — identify errors for fix
                    try:
                        tokenized_no_val = parse_tokenized_file(token_txt_path)
                        raw_lines = raw_text.strip().split("\n")
                        report = validate_full(stripped_sentences, tokenized_no_val, raw_lines)
                    except ValueError:
                        if attempt >= MAX_TOKENIZE_ATTEMPTS - 1:
                            raise
                        log.info("  Tokenization unparseable (attempt %d/%d), retrying...",
                                 attempt + 1, MAX_TOKENIZE_ATTEMPTS)
                        report = ValidationReport(
                            bad_indices=list(range(len(stripped_sentences))),
                        )
                        continue

                    if report.is_clean:
                        tokenized = tokenized_no_val
                        break

                    if attempt >= MAX_TOKENIZE_ATTEMPTS - 1:
                        raise ValueError(
                            f"Tokenization failed after {MAX_TOKENIZE_ATTEMPTS} attempts: "
                            f"{len(report.bad_indices)}/{len(stripped_sentences)} errors"
                        )

                    log.info("  Tokenization: %d/%d errors, fixing remaining (attempt %d/%d)...",
                             len(report.bad_indices), len(stripped_sentences),
                             attempt + 2, MAX_TOKENIZE_ATTEMPTS)

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

        log.info("event=pipeline_step step=tokenize chapter=%s sentences=%d retries=%d duration_ms=%d",
                 source_file_stem, len(tokenized) if 'tokenized' in locals() else 0,
                 tokenize_retries, int((time.time() - t_tokenize) * 1000))

        # -- 5. Audio --
        t_audio = time.time()
        if not config.skip_audio:
            audiobook_mp3_path = Path(audiobook_dir) / Path(source_file_stem + ".mp3")
            os.makedirs(audio_dir, exist_ok=True)
            os.makedirs(audiobook_dir, exist_ok=True)
            os.makedirs(chunks_dir, exist_ok=True)

            if not audiobook_mp3_path.exists():
                if service_manager:
                    service_manager.stop_if_running('llm')
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
                        book=book,
                        author=author,
                    )
                else:
                    # Fallback: per-sentence flow
                    process_json_to_audio(
                        source_txt_path,
                        os.path.join(audiobook_dir, source_file_stem + ".mp3"),
                        standalone_file=os.path.join(audio_dir, source_file_stem),
                        profile_key=config.audio_profile,
                        book=book,
                        author=author,
                    )

                if service_manager:
                    service_manager.stop_if_running('tts')
                    if llm_local:
                        dict_profile = _resolve_profile(config.task_profiles, 'dictionary')
                        service_manager.ensure_llm_profile(dict_profile)

            n_chunks = 0
            if chunk_json_path.exists():
                try:
                    n_chunks = len(load_chunks_json(chunk_json_path))
                except Exception:
                    pass
            log.info("event=pipeline_step step=audio chapter=%s chunks=%d duration_ms=%d",
                     source_file_stem, n_chunks, int((time.time() - t_audio) * 1000))

        # -- 6. Dictionary --
        t_dict = time.time()
        dict_count_before = len(dictionary)
        if service_manager and llm_local:
            dict_profile = _resolve_profile(config.task_profiles, 'dictionary')
            service_manager.ensure_llm_profile(dict_profile)

        process_json_file(token_txt_path, dictionary, config.dict_file,
                          config.resolve_prompt("dict_entry"),
                          models=config.models,
                          punctuation_skip=config.punctuation_skip,
                          extra_options=_resolve_extra_options(config, "dictionary"))
        dict_words_added = len(dictionary) - dict_count_before
        log.info("event=pipeline_step step=dict chapter=%s words=%d duration_ms=%d",
                 source_file_stem, dict_words_added, int((time.time() - t_dict) * 1000))

    update_manifest(config.books_dir)

    total_ms = int((time.time() - t_pipeline_start) * 1000)
    log.info("event=book_complete book=%s author=%s chapters=%d total_duration_ms=%d",
             book, author, processed, total_ms)

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