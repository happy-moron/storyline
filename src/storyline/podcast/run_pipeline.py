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
import tempfile
import time
from pathlib import Path

import storyline.logging
from storyline.config.pipeline_config import PipelineConfig
from storyline.logging import get_logger
from storyline.podcast.create_ereader import create_ereader
from storyline.podcast.create_mp3 import create_mp3
from storyline.podcast.selection import (
    Selection,
    append_episode,
    load_existing_episodes,
    load_hsk_grammar_points,
    load_topics,
    select_next,
)
from storyline.prompt_utils.run_prompt import run_prompt_with_metrics
from storyline.services.manager import ServiceManager


HSK_INDEX_DIR = "dict"
TOPICS_PATH = "src/storyline/podcast/topics.md"
EPISODES_PATH = "src/storyline/podcast/existing-episodes.csv"
SCRIPT_DIR = "books_src/podcasts"


def _resolve_profile(config: PipelineConfig, task: str) -> str | None:
    profiles = config.task_profiles
    if not profiles:
        return None
    default = profiles.get("default") or profiles.get("translate")
    return profiles.get(task, default)


def _ensure_llm(
    service_manager: ServiceManager | None,
    config: PipelineConfig,
    task: str,
) -> None:
    if service_manager and config.llm_provider == "local":
        service_manager.ensure_llm_profile(_resolve_profile(config, task))


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


def build_script_input(selection: Selection, vocab_text: str) -> str:
    lines = ["## HSK Point(s)", ""]
    for point in selection.grammar_points:
        lines.append(
            json.dumps([point.id, point.title, point.pattern], ensure_ascii=False)
        )
    lines += [
        "",
        "## Theme",
        "",
        selection.theme,
        "",
    ]
    return "\n".join(lines)


def generate_podcast(
    config: PipelineConfig,
    service_manager: ServiceManager | None = None,
    *,
    hsk_index_dir: str = HSK_INDEX_DIR,
    topics_path: str = TOPICS_PATH,
    episodes_path: str = EPISODES_PATH,
    script_dir: str = SCRIPT_DIR,
    flashcards: bool = True,
):
    log = get_logger("podcast")

    selection = select_next(
        load_hsk_grammar_points(Path(hsk_index_dir)),
        load_topics(Path(topics_path)),
        load_existing_episodes(Path(episodes_path)),
    )
    log.info(
        "event=selection episode=%d theme=%s grammar=%s",
        selection.episode_id,
        selection.theme_slug,
        ",".join(p.id for p in selection.grammar_points),
    )

    _ensure_llm(service_manager, config, "podcast_script")

    script_output = Path(script_dir) / f"{selection.theme_slug}.txt"
    script_output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        script_input = tmp_path / "script_input.txt"
        script_input.write_text(
            build_script_input(selection, ""), encoding="utf-8"
        )
        metrics = run_prompt_with_metrics(
            config.resolve_prompt("podcast_script"),
            script_input,
            script_output,
            models=config.models,
            timeout=config.llm_timeout_s,
            prompt_label="podcast_script",
        )
        log.info(
            "event=script_written theme=%s chars=%d wall_ms=%d",
            selection.theme_slug, script_output.stat().st_size, metrics.get("wall_ms", 0),
        )

        _ensure_llm(service_manager, config, "podcast_fix_script")

        _validate_and_fix(config, script_output, tmp_path, log)

    flashcard_output_dir: Path | None = None

    if flashcards and service_manager is not None:
        log.info("event=flashcard_vocab_stage")
        t0 = time.time()
        flashcard_output_dir = (
            Path(script_dir).parent.parent / "books" / "podcasts"
            / selection.theme_slug / "flashcards"
        )
        from storyline.flashcard.run_pipeline import (
            _extract_entries,
            _write_text_files_and_collect_prompts,
        )

        entries = _extract_entries(
            script_path=script_output,
            prompt_template_path="prompts/flashcard-vocab-from-script.md",
            service_manager=service_manager,
            models=config.models,
            output_dir=flashcard_output_dir,
            force=False,
            llm_already_running=True,
        )
        _write_text_files_and_collect_prompts(flashcard_output_dir, entries)
        log.info(
            "event=flashcard_vocab_done theme=%s entries=%d duration_ms=%d",
            selection.theme_slug,
            len(entries),
            int((time.time() - t0) * 1000),
        )

    append_episode(Path(episodes_path), selection)

    return {
        "selection": selection,
        "script_output": script_output,
        "flashcard_dir": flashcard_output_dir if flashcards else None,
    }


def _build_fix_input(script_text: str, error_message: str) -> str:
    return script_text + "\n\n# Error Messages/Logs\n\n" + error_message + "\n"


def _validate_and_fix(
    config: PipelineConfig,
    script_output: Path,
    tmp_path: Path,
    log,
) -> None:
    from storyline.podcast.script_parser import parse_script, ScriptParseError

    script_text = script_output.read_text(encoding="utf-8")
    try:
        parse_script(script_text)
        log.info("event=script_valid theme=%s", script_output.stem)
        return
    except ScriptParseError as e:
        log.warning("event=script_parse_error theme=%s error=%s", script_output.stem, str(e))

    fixed_text = _fix_script_by_sections(
        config, script_text, tmp_path, log, script_output.stem
    )

    script_output.write_text(fixed_text, encoding="utf-8")
    log.info("event=fixed_script_written theme=%s", script_output.stem)

    try:
        parse_script(fixed_text)
        log.info("event=fixed_script_valid theme=%s", script_output.stem)
    except ScriptParseError as e:
        raise RuntimeError(
            f"Fixed script still fails validation for {script_output.stem}: {e}"
        ) from e


def _fix_script_by_sections(
    config: PipelineConfig,
    script_text: str,
    tmp_path: Path,
    log,
    theme: str,
) -> str:
    from storyline.podcast.script_parser import parse_script, ScriptParseError
    from storyline.podcast.script_fix import plan_fixes, stitch_script

    current = script_text
    max_rounds = max(1, config.llm_retries)

    for round_no in range(1, max_rounds + 1):
        plan = plan_fixes(current)
        if not plan.fixes:
            break

        bodies = dict(plan.good)
        for fix in plan.fixes:
            bodies[fix.section] = _run_section_fix(
                config, fix, current, tmp_path, log, theme
            )
        current = stitch_script(bodies)
        log.info(
            "event=section_fix_round theme=%s round=%d fixes=%s",
            theme, round_no, ",".join(fix.section for fix in plan.fixes),
        )

        try:
            parse_script(current)
            return current
        except ScriptParseError:
            continue

    return current


def _run_section_fix(
    config: PipelineConfig,
    fix,
    full_script_text: str,
    tmp_path: Path,
    log,
    theme: str,
) -> str:
    raw = fix.raw if fix.raw.strip() else full_script_text
    safe_section = fix.section.lower().replace(" ", "_")

    fix_input_path = tmp_path / f"fix_{safe_section}_input.txt"
    fix_input_path.write_text(_build_fix_input(raw, fix.error), encoding="utf-8")

    fix_output_path = tmp_path / f"fix_{safe_section}_output.txt"
    metrics = run_prompt_with_metrics(
        config.resolve_prompt(fix.prompt_name),
        fix_input_path,
        fix_output_path,
        models=config.models,
        timeout=config.llm_timeout_s,
        prompt_label=fix.prompt_name,
    )
    log.info(
        "event=section_fix_prompt theme=%s section=%s wall_ms=%d",
        theme, fix.section, metrics.get("wall_ms", 0),
    )

    fixed_text = fix_output_path.read_text(encoding="utf-8").strip()
    if fixed_text.upper() == "GARBAGE":
        raise RuntimeError(
            f"Fix prompt returned GARBAGE for {fix.section} in {theme}: "
            f"section unfixable"
        )

    return _strip_section_header(fixed_text, fix.section)


def _strip_section_header(text: str, section: str) -> str:
    body = text.strip()
    lines = body.split("\n")
    if lines and lines[0].strip().upper() == section.upper():
        body = "\n".join(lines[1:]).strip()
    return body + "\n"


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
            generate_flashcard_audio(
                flashcard_dir, entries,
                service_manager=service_manager,
                use_omnivoice=use_omnivoice,
                use_builtin_hosts=use_builtin_hosts,
            )
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