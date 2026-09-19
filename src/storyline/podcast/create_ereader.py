import json
import os
import time
from pathlib import Path

import storyline.logging
from storyline.logging import get_logger
from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.book.cjk_punct import strip as strip_cjk_punct, reinsert as reinsert_cjk_punct, format_compact
from storyline.book.parse_pipe_format import parse_source_file
from storyline.book.tokenization_repair import ValidationReport, validate_full
from storyline.book.create_custom_dict import load_dictionary, process_json_file
from storyline.book.update_manifest import update_manifest
from storyline.config.pipeline_config import PipelineConfig
from storyline.podcast.audio_gen import add_word_timings, generate_dialogue_audio
from storyline.podcast.omnivoice_audio import OmnivoiceTTSService
from storyline.podcast.script_parser import parse_script
from storyline.podcast.selection import normalize_name
from storyline.prompt_utils.run_prompt import run_prompt_with_metrics
from storyline.services.manager import ServiceManager


def _resolve_profile(config: PipelineConfig, task: str) -> str | None:
    profiles = config.task_profiles
    if not profiles:
        return None
    default = profiles.get("default") or profiles.get("translate")
    return profiles.get(task, default)


def _resolve_extra_options(config: PipelineConfig, task: str) -> dict | None:
    budget = config.resolve_thinking_budget(task)
    if budget is not None and budget > 0:
        return {"thinking_budget_tokens": budget}
    return None


def _ensure_llm(service_manager: ServiceManager | None, config: PipelineConfig,
                task: str) -> None:
    if service_manager and config.llm_provider == "local":
        service_manager.ensure_llm_profile(_resolve_profile(config, task))


def _llm_call(*, prompt_key: str, input_path: Path, output_path: Path,
              config: PipelineConfig, task: str,
              prompt_label: str, log, stem: str, attempt: int) -> dict:
    chars_in = len(input_path.read_text(encoding="utf-8").strip())
    metrics = run_prompt_with_metrics(
        config.resolve_prompt(prompt_key),
        input_path, output_path,
        models=config.models, timeout=config.llm_timeout_s,
        prompt_label=prompt_label,
        extra_options=_resolve_extra_options(config, task),
    )
    parts = [f"event=llm_call prompt={prompt_label} chapter={stem} wall_ms={metrics['wall_ms']}"]
    for k in ("prompt_tokens", "eval_tokens", "prompt_tps", "eval_tps",
              "server_total_ms", "cache", "model_hint"):
        if k in metrics:
            parts.append(f"{k}={metrics[k]}")
    log.info(" ".join(parts))
    return metrics


def _slug_from_filename(filename: str) -> str:
    base = os.path.splitext(os.path.basename(filename))[0]
    return normalize_name(base)


def _title_from_slug(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()


def create_ereader(
    script_path: str,
    config: PipelineConfig,
    service_manager: ServiceManager | None = None,
    *,
    tts_service: "Qwen3TTSService | OmnivoiceTTSService | None" = None,
    use_omnivoice: bool = False,
):
    storyline.logging.init()
    log = get_logger("podcast.ereader")

    slug = _slug_from_filename(script_path)
    title = _title_from_slug(slug)
    author = "podcasts"

    base_dir = Path(config.book_dir(author, slug))
    pipe_source_dir = base_dir / "pipe" / "source"
    pipe_token_dir = base_dir / "pipe" / "tokenized"
    chunks_dir = base_dir / "chunks"

    os.makedirs(pipe_source_dir, exist_ok=True)
    os.makedirs(pipe_token_dir, exist_ok=True)
    os.makedirs(chunks_dir, exist_ok=True)

    stem = f"{slug}_1"
    log.info("event=podcast_ereader_start episode=%s", slug)

    # ── 1. Parse the script ────────────────────────────────────────────
    t0 = time.time()
    script_text = Path(script_path).read_text(encoding="utf-8")
    script = parse_script(script_text)
    dialogue = script.dialogue
    log.info(
        "event=ereader_step step=parse lines=%d duration_ms=%d",
        len(dialogue), int((time.time() - t0) * 1000),
    )

    if not dialogue:
        raise ValueError(f"No dialogue lines found in script: {script_path}")

    # ── 2. Write pipe/source (zh|||en) ─────────────────────────────────
    t0 = time.time()
    source_path = pipe_source_dir / f"{stem}.txt"
    pipe_lines = [f"{line.chinese}|||{line.english}" for line in dialogue]
    source_path.write_text("\n".join(pipe_lines) + "\n", encoding="utf-8")
    log.info(
        "event=ereader_step step=source_write pairs=%d duration_ms=%d",
        len(dialogue), int((time.time() - t0) * 1000),
    )

    # ── 3. Tokenize ────────────────────────────────────────────────────
    t0 = time.time()
    source_sentences = parse_source_file(source_path)
    chinese_sentences = [
        "".join(item["chinese"].split()) for item in source_sentences
    ]

    stripped_sentences = []
    punct_maps = []
    for ch in chinese_sentences:
        stripped, positions = strip_cjk_punct(ch)
        stripped_sentences.append(stripped)
        punct_maps.append(positions)

    token_path = pipe_token_dir / f"{stem}.txt"
    chinese_input_path = pipe_token_dir / f"{stem}_input.txt"
    chinese_input_path.write_text("\n".join(stripped_sentences), encoding="utf-8")

    _ensure_llm(service_manager, config, 'tokenize')

    from storyline.book.parse_pipe_format import parse_tokenized_file

    MAX_ATTEMPTS = 3
    tokenized = None
    n_sentences = len(stripped_sentences)

    for attempt in range(MAX_ATTEMPTS):
        if attempt == 0:
            _llm_call(
                prompt_key="tokenize", input_path=chinese_input_path,
                output_path=token_path, config=config,
                task="tokenize", prompt_label="tokenize",
                log=log, stem=stem, attempt=attempt + 1,
            )

        raw_text = token_path.read_text(encoding="utf-8")

        # Try strict validation first
        try:
            tokenized = parse_tokenized_file(
                token_path, expected_chinese=stripped_sentences,
            )
            break
        except ValueError:
            pass

        # Try lenient parse + manual validation
        try:
            tokenized_no_val = parse_tokenized_file(token_path)
            raw_lines = raw_text.strip().split("\n")
            report = validate_full(
                stripped_sentences, tokenized_no_val, raw_lines,
            )
        except ValueError:
            report = ValidationReport(
                bad_indices=list(range(n_sentences)),
            )

        if report.is_clean:
            tokenized = tokenized_no_val
            break

        if attempt >= MAX_ATTEMPTS - 1:
            raise ValueError(
                f"Tokenization failed after {MAX_ATTEMPTS} attempts: "
                f"{len(report.bad_indices)}/{n_sentences} errors"
            )

        log.info(
            "  Tokenization: %d/%d errors, fixing remaining (attempt %d/%d)...",
            len(report.bad_indices), n_sentences,
            attempt + 2, MAX_ATTEMPTS,
        )

        # -- Fix attempt: send bad sentences to fix prompt, parse result --
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
            task="tokenize", prompt_label="fix_tokenization",
            log=log, stem=stem, attempt=attempt + 1,
        )

        # Parse fix output through the full parser (handles compact & legacy)
        fix_text = fix_output.read_text(encoding="utf-8")
        fix_parsed_path = pipe_token_dir / f"{stem}_fix_parsed_{attempt}.txt"
        fix_parsed_path.write_text(fix_text, encoding="utf-8")
        fix_input.unlink()
        fix_output.unlink()

        try:
            fixed = parse_tokenized_file(fix_parsed_path)
        except ValueError:
            fix_parsed_path.unlink()
            log.info("  Fix output failed to parse; retrying all sentences.")
            # Treat all sentences as still bad and retry from scratch
            continue

        fix_parsed_path.unlink()

        # Splice fixed entries into the best-guess lines we have
        fixed_by_index: dict[int, str] = {}
        for j, idx in enumerate(target_indices):
            if j < len(fixed):
                fixed_by_index[idx] = format_compact(fixed[j]["t"])

        # Rebuild raw_lines if we have them, otherwise start from scratch
        all_lines: list[str]
        try:
            all_lines = raw_text.strip().split("\n")
        except UnboundLocalError:
            all_lines = [""] * n_sentences

        while len(all_lines) < n_sentences:
            all_lines.append("")

        for idx, compact in fixed_by_index.items():
            all_lines[idx] = compact

        token_path.write_text("\n".join(all_lines) + "\n", encoding="utf-8")

    chinese_input_path.unlink()

    # Reinsert CJK punctuation
    for i, sentence_data in enumerate(tokenized):
        original = chinese_sentences[i]
        sentence_data["t"] = reinsert_cjk_punct(
            sentence_data["t"], punct_maps[i], original,
        )

    with open(token_path, "w", encoding="utf-8") as f:
        for sentence_data in tokenized:
            f.write(format_compact(sentence_data["t"]) + "\n")

    log.info(
        "event=ereader_step step=tokenize sentences=%d attempts=%d duration_ms=%d",
        len(tokenized), attempt + 1, int((time.time() - t0) * 1000),
    )

    # ── 4. Create chunks (one chunk per dialogue line) ─────────────────
    t0 = time.time()
    chunk_json_path = chunks_dir / f"{stem}.json"
    chunks = []
    for i in range(len(dialogue)):
        chunks.append({
            "instruct": "",
            "instruct_zh": "",
            "line_range": [i, i],
        })
    with open(chunk_json_path, "w", encoding="utf-8") as f:
        json.dump({"chunks": chunks}, f, ensure_ascii=False, indent=2)
    log.info(
        "event=ereader_step step=chunks n=%d duration_ms=%d",
        len(chunks), int((time.time() - t0) * 1000),
    )

    # ── 5. Dictionary ──────────────────────────────────────────────────
    t0 = time.time()
    dictionary = load_dictionary(config.dict_file)
    words_before = len(dictionary)
    _ensure_llm(service_manager, config, 'dictionary')
    process_json_file(
        token_path, dictionary, config.dict_file,
        config.resolve_prompt("dict_entry"),
        models=config.models,
        punctuation_skip=config.punctuation_skip,
        extra_options=_resolve_extra_options(config, "dictionary"),
    )
    words_added = len(dictionary) - words_before
    log.info(
        "event=ereader_step step=dict words_added=%d duration_ms=%d",
        words_added, int((time.time() - t0) * 1000),
    )

    # ── 6. Audio (voice design via Qwen3 + voice clone) ─────────────
    if not config.skip_audio:
        t0 = time.time()

        # Voice design always uses Qwen3
        if service_manager:
            service_manager.stop_if_running('llm')
            service_manager.start_if_needed('tts')
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

        try:
            n_audio = generate_dialogue_audio(
                script, slug, base_dir,
                service=host_service,
                clone_service=clone_service,
                service_manager=service_manager if use_omnivoice else None,
            )

            # ── 6a. Forced alignment (word timings) ──────────────────
            if not config.skip_backchain:
                t0_align = time.time()
                n_aligned = add_word_timings(
                    chunk_json_path, base_dir, script, host_service,
                )
                log.info(
                    "event=ereader_step step=align chunks=%d duration_ms=%d",
                    n_aligned, int((time.time() - t0_align) * 1000),
                )
        finally:
            if service_manager:
                service_manager.stop_if_running('tts')
        log.info(
            "event=ereader_step step=audio chunks=%d duration_ms=%d",
            n_audio, int((time.time() - t0) * 1000),
        )

    # ── 6b. Backchain generation ────────────────────────────────────
    if not config.skip_audio and not config.skip_backchain:
        t0 = time.time()
        if service_manager:
            service_manager.start_if_needed('llm')
        _ensure_llm(service_manager, config, 'backchain')

        from storyline.podcast.backchain import generate_backchains

        chinese_lines = [line.chinese for line in dialogue]
        chinese_lines = [l for l in chinese_lines if l.strip()]

        try:
            results = generate_backchains(
                chinese_lines, slug, base_dir,
                resolve_prompt=config.resolve_prompt,
                models=config.models,
                llm_timeout_s=config.llm_timeout_s,
                llm_retries=config.llm_retries,
                extra_options=_resolve_extra_options(config, "backchain"),
            )

            # Fix backchain step boundaries to align with tokenized words.
            # Build token_texts aligned with chinese_lines (same filtering).
            from storyline.podcast.backchain import fix_token_boundaries
            token_texts = [
                [t[0] for t in tokenized[i]["t"]]
                for i, line in enumerate(dialogue)
                if line.chinese.strip()
            ]
            results = fix_token_boundaries(results, token_texts)

            with open(chunk_json_path, encoding="utf-8") as f:
                chunk_data = json.load(f)
            for r in results:
                if r.line_index < len(chunk_data["chunks"]):
                    chunk = chunk_data["chunks"][r.line_index]
                    steps = [s.text for s in r.steps[:-1]]  # exclude final (full sentence)
                    chunk["lines"][0]["backchain"] = steps
            with open(chunk_json_path, "w", encoding="utf-8") as f:
                json.dump(chunk_data, f, ensure_ascii=False, indent=2)

            log.info(
                "event=ereader_step step=backchain lines=%d duration_ms=%d",
                len(results), int((time.time() - t0) * 1000),
            )
        except Exception as e:
            log.warning(
                "event=backchain_failure error=%s", str(e),
            )

    # ── 6c. Breakdown generation ─────────────────────────────────────
    if not config.skip_audio and not config.skip_backchain:
        t0 = time.time()
        if service_manager:
            service_manager.start_if_needed('llm')
        _ensure_llm(service_manager, config, 'translate')

        chinese_lines = [line.chinese for line in dialogue]
        chinese_lines = [l for l in chinese_lines if l.strip()]

        try:
            breakdowns = generate_breakdown_for_chapter(
                chinese_lines, stem, base_dir,
                resolve_prompt=config.resolve_prompt,
                models=config.models,
                llm_timeout_s=config.llm_timeout_s,
                llm_retries=config.llm_retries,
                extra_options=_resolve_extra_options(config, "translate"),
            )

            # Store breakdown in chunk JSON
            with open(chunk_json_path, encoding="utf-8") as f:
                chunk_data = json.load(f)
            for ci, chunk in enumerate(chunk_data["chunks"]):
                line_range = chunk.get("line_range", [])
                if len(line_range) != 2:
                    continue
                line_idx = line_range[0]
                if line_idx < len(breakdowns) and breakdowns[line_idx]:
                    if chunk.get("lines"):
                        chunk["lines"][0]["breakdown"] = breakdowns[line_idx]
            with open(chunk_json_path, "w", encoding="utf-8") as f:
                json.dump(chunk_data, f, ensure_ascii=False, indent=2)

            n_covered = sum(1 for b in breakdowns if b)
            log.info(
                "event=ereader_step step=breakdown lines=%d covered=%d duration_ms=%d",
                len(chinese_lines), n_covered, int((time.time() - t0) * 1000),
            )
        except Exception as e:
            log.warning(
                "event=breakdown_failure error=%s", str(e),
            )

    # ── 7. Update manifest ─────────────────────────────────────────────
    t0 = time.time()
    update_manifest(config.books_dir)
    log.info(
        "event=ereader_step step=manifest duration_ms=%d",
        int((time.time() - t0) * 1000),
    )

    log.info(
        "event=podcast_ereader_complete episode=%s lines=%d",
        slug, len(dialogue),
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Generate e-reader files from a podcast script"
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
        "-m", "--models", type=str, default=None,
        help="Comma-separated list of models (overrides config)",
    )
    parser.add_argument(
        '--skip-audio', dest='skip_audio', default=None,
        action=argparse.BooleanOptionalAction, help='Skip audio generation',
    )
    parser.add_argument(
        '--skip-backchain', dest='skip_backchain', default=None,
        action=argparse.BooleanOptionalAction, help='Skip back-chain generation and forced alignment',
    )
    parser.add_argument(
        '--omnivoice', action='store_true',
        help='Use omnivoice-infer (CUDA/GPU) for voice cloning instead of Qwen3-TTS',
    )
    args = parser.parse_args()

    config = PipelineConfig.from_files_and_args(args, profile_name=args.profile)
    service_manager = ServiceManager()

    try:
        create_ereader(args.input, config, service_manager=service_manager, use_omnivoice=args.omnivoice)
    finally:
        if config.llm_provider == "local":
            service_manager.stop("llm")