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
from storyline.prompt_utils.run_prompt import run_prompt_with_metrics
from storyline.book.create_custom_dict import process_json_file, load_dictionary
from storyline.services.manager import ServiceManager
from storyline.audio.audiobook_gen_qwen3 import process_json_to_audio
from storyline.audio.audiobook_gen_chunk import process_chapter_chunks
from storyline.book.tokenization_repair import (
    ValidationReport,
    validate_full,
    apply_fixes,
)
from storyline.book.update_manifest import update_manifest
from storyline.config.pipeline_config import PipelineConfig
from zsp_llm_client.prompt_runner import PromptRunner


# ============================================================================
# Shared helpers
# ============================================================================

def _resolve_profile(task_profiles: dict, task: str) -> str | None:
    if not task_profiles:
        return None
    default = task_profiles.get("default") or task_profiles.get("translate")
    return task_profiles.get(task, default)


def _resolve_extra_options(config: PipelineConfig, task: str) -> dict | None:
    budget = config.resolve_thinking_budget(task)
    if budget is not None and budget > 0:
        return {"thinking_budget_tokens": budget}
    return None


def _log_llm_call(log, prompt: str, chapter: str, metrics: dict,
                  chars_in=None, attempt=None):
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


def _ensure_llm(service_manager: ServiceManager | None, config: PipelineConfig,
                profile_key: str) -> None:
    if service_manager and config.llm_provider == "local":
        profile = _resolve_profile(config.task_profiles, profile_key)
        service_manager.ensure_llm_profile(profile)


def _llm_call(*, prompt_key: str, input_path: Path, output_path: Path,
              config: PipelineConfig, profile_key: str,
              prompt_label: str, log, stem: str, attempt: int) -> dict:
    """Single LLM call with metrics and structured logging."""
    chars_in = len(Path(input_path).read_text(encoding="utf-8").strip())
    metrics = run_prompt_with_metrics(
        config.resolve_prompt(prompt_key),
        input_path, output_path,
        models=config.models, timeout=config.llm_timeout_s,
        prompt_label=prompt_label,
        extra_options=_resolve_extra_options(config, profile_key),
    )
    _log_llm_call(log, prompt_label, stem, metrics,
                   chars_in=chars_in, attempt=attempt)
    return metrics


def _retry_llm(*, prompt_key: str, input_path: Path, output_path: Path,
               config: PipelineConfig, service_manager: ServiceManager | None,
               profile_key: str, prompt_label: str, log, stem: str,
               is_valid, error_label: str) -> int:
    """Run an LLM prompt with retry. *is_valid*(output_path) must return True on success.

    Returns the attempt number (1-based) on which the call succeeded.
    """
    _ensure_llm(service_manager, config, profile_key)
    for attempt in range(config.llm_retries):
        _llm_call(prompt_key=prompt_key, input_path=input_path,
                   output_path=output_path, config=config,
                   profile_key=profile_key, prompt_label=prompt_label,
                   log=log, stem=stem, attempt=attempt + 1)
        if is_valid(output_path):
            return attempt + 1
        if attempt < config.llm_retries - 1:
            log.info("  %s (attempt %d/%d), retrying...",
                     error_label, attempt + 1, config.llm_retries)
            os.remove(output_path)
        else:
            raise ValueError(f"{error_label} after {config.llm_retries} attempts")


def _natural_sort(files: list[Path]) -> list[Path]:
    def _num(f: Path) -> int:
        m = re.search(r'_(\d+)\.', f.name)
        return int(m.group(1)) if m else 0
    return sorted(files, key=_num)


# ============================================================================
# Pipeline stages
# ============================================================================

def split_stage(input_text: str, output_dir: str, chunk_size: int) -> list[Path]:
    """Split book into ~2000-char chapters. Returns sorted Path list."""
    split_text(input_text, output_dir, chunk_size=chunk_size)
    return _natural_sort(list(Path(output_dir).glob("*.txt")))


def simplify_stage(source_path: Path, simple_path: Path,
                   config: PipelineConfig,
                   service_manager: ServiceManager | None,
                   log, stem: str) -> int:
    """Simplify English to ~9th grade level. Returns number of output lines."""
    _retry_llm(
        prompt_key="simplify",
        input_path=source_path, output_path=simple_path,
        config=config, service_manager=service_manager,
        profile_key="translate", prompt_label="simplify",
        log=log, stem=stem,
        is_valid=lambda p: p.read_text(encoding="utf-8").strip(),
        error_label="Simplify produced empty output",
    )
    slines = simple_path.read_text(encoding="utf-8").strip().split("\n")
    clean = [l for l in slines if l.strip()]
    if len(clean) != len(slines):
        simple_path.write_text("\n".join(clean) + "\n", encoding="utf-8")
    return len(clean)


def chunk_stage(chunk_input: Path, chunks_dir: Path, stem: str,
                config: PipelineConfig,
                service_manager: ServiceManager | None,
                log) -> tuple[int, int]:
    """Group lines into natural-reading chunks. Returns (n_chunks, n_lines)."""
    chunk_json_path = chunks_dir / f"{stem}.json"
    chunk_raw_path = chunks_dir / f"{stem}_raw.txt"

    # Number lines so the LLM can reference line ranges
    clines = chunk_input.read_text(encoding="utf-8").strip().split("\n")
    nonempty = [l.strip() for l in clines if l.strip()]
    numbered = [f"{i+1}: {line}" for i, line in enumerate(nonempty)]
    numbered_path = chunks_dir / f"{stem}_numbered.txt"
    numbered_path.write_text("\n".join(numbered) + "\n", encoding="utf-8")

    _retry_llm(
        prompt_key="chunk",
        input_path=numbered_path, output_path=chunk_raw_path,
        config=config, service_manager=service_manager,
        profile_key="translate", prompt_label="chunk",
        log=log, stem=stem,
        is_valid=lambda p: p.read_text(encoding="utf-8").strip(),
        error_label="Chunking produced empty output",
    )

    raw_text = chunk_raw_path.read_text(encoding="utf-8").strip()
    chunks = parse_chunk_format(raw_text, nonempty)
    save_chunks_json(chunks, chunk_json_path)
    return len(chunks), len(nonempty)


def translate_stage(translate_input: Path, pipe_output_path: Path,
                    config: PipelineConfig,
                    service_manager: ServiceManager | None,
                    log, stem: str) -> int:
    """Translate to Chinese, one line per sentence. Returns sentence-pair count."""
    english_lines = [
        l.strip() for l in translate_input.read_text(encoding="utf-8").strip().split("\n")
        if l.strip()
    ]

    def _validate_and_pair(p: Path) -> bool:
        """Pair Chinese output with English source, write ||| format, then validate."""
        chinese_lines = p.read_text(encoding="utf-8").strip().split("\n")
        pipe_lines = []
        for i, ch in enumerate(chinese_lines):
            ch = ch.strip()
            if not ch:
                continue
            en = english_lines[i] if i < len(english_lines) else ""
            pipe_lines.append(f"{ch}|||{en.strip()}")
        p.write_text("\n".join(pipe_lines) + "\n", encoding="utf-8")
        try:
            parse_source_file(p)
            return True
        except ValueError:
            return False

    _retry_llm(
        prompt_key="translate",
        input_path=translate_input, output_path=pipe_output_path,
        config=config, service_manager=service_manager,
        profile_key="translate", prompt_label="translate",
        log=log, stem=stem,
        is_valid=_validate_and_pair,
        error_label="Translation parse failed",
    )
    return len(parse_source_file(pipe_output_path))


def tokenize_stage(source_txt_path: Path, token_txt_path: Path,
                   config: PipelineConfig,
                   service_manager: ServiceManager | None,
                   log, stem: str) -> tuple[int, int]:
    """POS-annotate Chinese tokens with pinyin. Returns (sentence_count, retry_count)."""
    pipe_token_dir = token_txt_path.parent

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

    chinese_input_path = pipe_token_dir / f"{stem}_input.txt"
    chinese_input_path.write_text("\n".join(stripped_sentences), encoding="utf-8")

    _ensure_llm(service_manager, config, 'tokenize')

    MAX_ATTEMPTS = 3
    tokenized = None
    raw_lines = []
    report = None

    for attempt in range(MAX_ATTEMPTS):
        if attempt == 0:
            _llm_call(
                prompt_key="tokenize", input_path=chinese_input_path,
                output_path=token_txt_path, config=config,
                profile_key="tokenize", prompt_label="tokenize",
                log=log, stem=stem, attempt=attempt + 1,
            )
        else:
            target_indices = sorted(report.bad_indices)
            if not target_indices:
                continue
            bad_sentences = [stripped_sentences[i] for i in target_indices]

            fix_input = pipe_token_dir / f"{stem}_fix_input_{attempt}.txt"
            fix_output = pipe_token_dir / f"{stem}_fix_output_{attempt}.txt"
            fix_input.write_text("\n".join(bad_sentences), encoding="utf-8")

            _llm_call(
                prompt_key="fix_tokenization", input_path=fix_input,
                output_path=fix_output, config=config,
                profile_key="tokenize", prompt_label="fix_tokenization",
                log=log, stem=stem, attempt=attempt + 1,
            )

            fix_text = fix_output.read_text(encoding="utf-8")
            raw_lines = apply_fixes(raw_lines, fix_text, target_indices)
            token_txt_path.write_text("\n".join(raw_lines) + "\n", encoding="utf-8")

            fix_input.unlink()
            fix_output.unlink()

        raw_text = token_txt_path.read_text(encoding="utf-8")

        try:
            tokenized = parse_tokenized_file(
                token_txt_path, expected_chinese=stripped_sentences,
            )
            break
        except ValueError:
            try:
                tokenized_no_val = parse_tokenized_file(token_txt_path)
                raw_lines = raw_text.strip().split("\n")
                report = validate_full(
                    stripped_sentences, tokenized_no_val, raw_lines,
                )
            except ValueError:
                if attempt >= MAX_ATTEMPTS - 1:
                    raise
                report = ValidationReport(
                    bad_indices=list(range(len(stripped_sentences))),
                )
                continue

            if report.is_clean:
                tokenized = tokenized_no_val
                break

            if attempt >= MAX_ATTEMPTS - 1:
                raise ValueError(
                    f"Tokenization failed after {MAX_ATTEMPTS} attempts: "
                    f"{len(report.bad_indices)}/{len(stripped_sentences)} errors"
                )

            log.info(
                "  Tokenization: %d/%d errors, fixing remaining (attempt %d/%d)...",
                len(report.bad_indices), len(stripped_sentences),
                attempt + 2, MAX_ATTEMPTS,
            )

    chinese_input_path.unlink()

    # Reinsert CJK punctuation removed before tokenization
    for i, sentence_data in enumerate(tokenized):
        original = chinese_sentences[i]
        sentence_data["t"] = reinsert_cjk_punct(
            sentence_data["t"], punct_maps[i], original,
        )

    with open(token_txt_path, "w", encoding="utf-8") as f:
        for sentence_data in tokenized:
            f.write(format_compact(sentence_data["t"]) + "\n")

    return len(tokenized), attempt


def audio_stage(source_txt_path: Path, english_input: Path,
                chunk_json_path: Path, chunks_dir: Path,
                audio_dir: Path, audiobook_mp3_path: Path,
                config: PipelineConfig,
                service_manager: ServiceManager | None,
                book: str, author: str, log, stem: str) -> int | None:
    """Generate chunk-level TTS, forced alignment, and aggregate audiobook.

    Returns chunk count, or None if audio is skipped.
    """
    if config.skip_audio:
        return None

    if service_manager:
        service_manager.stop_if_running('llm')
        service_manager.start_if_needed('tts')

    if chunk_json_path.exists():
        process_chapter_chunks(
            source_txt_path, english_input, chunk_json_path,
            chapters_audio_dir=str(audio_dir),
            aggregate_output_path=str(audiobook_mp3_path),
            profile_key=config.audio_profile,
            book=book, author=author,
            use_instruct=config.audio_use_instruct,
        )
    else:
        process_json_to_audio(
            source_txt_path,
            str(audiobook_mp3_path),
            standalone_file=str(audio_dir / stem),
            profile_key=config.audio_profile,
            book=book, author=author,
        )

    if service_manager:
        service_manager.stop_if_running('tts')

    n_chunks = 0
    if chunk_json_path.exists():
        try:
            n_chunks = len(load_chunks_json(chunk_json_path))
        except Exception:
            pass
    return n_chunks


def dictionary_stage(token_txt_path: Path, dictionary: dict, dict_file: str,
                     config: PipelineConfig,
                     service_manager: ServiceManager | None,
                     log) -> int:
    """Build popup dictionary entries for unknown words. Returns words added."""
    before = len(dictionary)
    _ensure_llm(service_manager, config, 'dictionary')
    process_json_file(
        token_txt_path, dictionary, dict_file,
        config.resolve_prompt("dict_entry"),
        models=config.models,
        punctuation_skip=config.punctuation_skip,
        extra_options=_resolve_extra_options(config, "dictionary"),
    )
    return len(dictionary) - before


# ============================================================================
# Pipeline orchestrator
# ============================================================================

def create_book(input_text: str, author: str, config: PipelineConfig,
                service_manager: ServiceManager | None = None):
    book = os.path.splitext(os.path.basename(input_text))[0]
    storyline.logging.init()
    log = get_logger("book")

    t_start = time.time()
    llm_local = config.llm_provider == "local"

    base_dir = config.book_dir(author, book)
    split_source_dir = os.path.join(base_dir, "split", "source")
    simple_dir = os.path.join(base_dir, "split", "simple")
    chunks_dir = Path(base_dir) / "chunks"
    pipe_source_dir = os.path.join(base_dir, "pipe", "source")
    pipe_token_dir = os.path.join(base_dir, "pipe", "tokenized")
    audio_dir = Path(base_dir) / "audio"
    audiobook_dir = config.audiobook_output_dir(book)

    log.info("event=pipeline_start book=%s author=%s profile=%s",
             book, author, config.profile_name or "default")

    # -- Warmup (local LLM only) --
    if service_manager and llm_local and config.warmup_on_start:
        _ensure_llm(service_manager, config, 'translate')
        log.info("Warming up LLM (first call compiles CUDA graphs)...")
        PromptRunner().run(
            config.resolve_prompt("warmup"), "Hello.",
            models=config.models,
        )
        log.info("LLM warmup complete.")

    # -- 1. Split --
    t0 = time.time()
    chapter_files = split_stage(input_text, split_source_dir, config.split_chunk_size)
    if len(chapter_files) > config.max_chunks:
        chapter_files = chapter_files[:config.max_chunks]
    log.info("event=pipeline_step step=split chapters=%d duration_ms=%d",
             len(chapter_files), int((time.time() - t0) * 1000))

    os.makedirs(simple_dir, exist_ok=True)
    os.makedirs(chunks_dir, exist_ok=True)
    os.makedirs(pipe_source_dir, exist_ok=True)
    os.makedirs(pipe_token_dir, exist_ok=True)

    dictionary = load_dictionary(config.dict_file)

    # -- Per-chapter stages --
    for chapter_idx, source_file in enumerate(chapter_files):
        stem = os.path.splitext(os.path.basename(source_file))[0]
        log.info("processing %s", stem)

        simple_path = Path(simple_dir) / f"{stem}.txt"

        # -- 2. Simplify --
        t0 = time.time()
        if not config.skip_simplify and not simple_path.exists():
            n_lines = simplify_stage(
                source_file, simple_path, config, service_manager, log, stem,
            )
            log.info(
                "event=pipeline_step step=simplify chapter=%s lines=%d duration_ms=%d",
                stem, n_lines, int((time.time() - t0) * 1000),
            )
        elif config.skip_simplify:
            log.info("event=pipeline_step step=simplify chapter=%s skipped", stem)

        # Source for subsequent stages: simplified text if available, else raw
        stage_input = simple_path if (
            not config.skip_simplify and simple_path.exists()
        ) else source_file

        # -- 3. Chunk --
        t0 = time.time()
        chunk_json_path = chunks_dir / f"{stem}.json"
        if not chunk_json_path.exists():
            n_chunks, n_lines = chunk_stage(
                stage_input, chunks_dir, stem, config, service_manager, log,
            )
            log.info(
                "event=pipeline_step step=chunk chapter=%s lines=%d nchunks=%d duration_ms=%d",
                stem, n_lines, n_chunks, int((time.time() - t0) * 1000),
            )

        # -- 4. Translate --
        t0 = time.time()
        pipe_output_path = Path(pipe_source_dir) / f"{stem}.txt"
        if not pipe_output_path.exists():
            n_pairs = translate_stage(
                stage_input, pipe_output_path, config, service_manager, log, stem,
            )
            log.info(
                "event=pipeline_step step=translate chapter=%s pairs=%d duration_ms=%d",
                stem, n_pairs, int((time.time() - t0) * 1000),
            )

        # -- 5. Tokenize --
        t0 = time.time()
        token_path = Path(pipe_token_dir) / f"{stem}.txt"
        if not token_path.exists():
            n_sentences, n_retries = tokenize_stage(
                pipe_output_path, token_path, config, service_manager, log, stem,
            )
            log.info(
                "event=pipeline_step step=tokenize chapter=%s sentences=%d retries=%d duration_ms=%d",
                stem, n_sentences, n_retries, int((time.time() - t0) * 1000),
            )

        # -- 6. Audio --
        t0 = time.time()
        if not config.skip_audio:
            os.makedirs(audio_dir, exist_ok=True)
            os.makedirs(audiobook_dir, exist_ok=True)
            audiobook_mp3_path = Path(audiobook_dir) / f"{chapter_idx:03d}_{stem}.mp3"
            if not audiobook_mp3_path.exists():
                n_chunks = audio_stage(
                    pipe_output_path, stage_input, chunk_json_path,
                    chunks_dir, audio_dir, audiobook_mp3_path,
                    config, service_manager, book, author, log, stem,
                )
                log.info(
                    "event=pipeline_step step=audio chapter=%s chunks=%d duration_ms=%d",
                    stem, n_chunks or 0, int((time.time() - t0) * 1000),
                )

        # -- 7. Dictionary --
        t0 = time.time()
        words_added = dictionary_stage(
            token_path, dictionary, config.dict_file, config, service_manager, log,
        )
        log.info(
            "event=pipeline_step step=dict chapter=%s words=%d duration_ms=%d",
            stem, words_added, int((time.time() - t0) * 1000),
        )

    # -- Finalize --
    update_manifest(config.books_dir)

    total_ms = int((time.time() - t_start) * 1000)
    log.info("event=book_complete book=%s author=%s chapters=%d total_duration_ms=%d",
             book, author, len(chapter_files), total_ms)

    if service_manager and llm_local:
        service_manager.stop('llm')


# ============================================================================
# CLI entry point
# ============================================================================

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
        help="Pipeline profile from pipeline.toml (e.g. quick, test)",
    )
    parser.add_argument(
        '--skip-simplify', default=None, action=argparse.BooleanOptionalAction,
        dest='skip_simplify',
    )
    parser.add_argument(
        '--max-chunks', dest='max_chunks', type=int, default=None,
        help='max number of chapters',
    )
    parser.add_argument(
        '--skip-audio', dest='skip_audio', default=None,
        action=argparse.BooleanOptionalAction, help='Skip audio generation',
    )
    parser.add_argument(
        '--audio-profile', default=None,
        help='Audio profile from audio.toml (e.g. default, voice-clone)',
    )
    parser.add_argument(
        '--audio-use-instruct', default=None,
        action=argparse.BooleanOptionalAction,
        dest='audio_use_instruct',
        help='Pass chunk @instruct directions to TTS engine (overrides audio.toml)',
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