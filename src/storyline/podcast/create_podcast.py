import json
import tempfile
from pathlib import Path

import storyline.logging
from storyline.logging import get_logger
from storyline.config.pipeline_config import PipelineConfig
from storyline.prompt_utils.run_prompt import run_prompt_with_metrics
from storyline.services.manager import ServiceManager
from storyline.podcast.selection import (
    Selection,
    append_episode,
    load_existing_episodes,
    load_hsk_grammar_points,
    load_topics,
    select_next,
)


HSK_INDEX_DIR = "dict"
TOPICS_PATH = "src/storyline/podcast/topics.md"
EPISODES_PATH = "src/storyline/podcast/existing-episodes.csv"
SCRIPT_DIR = "books_src/podcasts"
VOCAB_DIR = "books_src/podcasts/vocab"


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


def build_vocab_input(theme: str) -> str:
    return theme + "\n"


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
        "## Vocab",
        "",
        vocab_text.strip(),
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
    vocab_dir: str = VOCAB_DIR,
):
    storyline.logging.init()
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

    append_episode(Path(episodes_path), selection)

    _ensure_llm(service_manager, config, "podcast_vocab")

    vocab_output = Path(vocab_dir) / f"{selection.theme_slug}.txt"
    script_output = Path(script_dir) / f"{selection.theme_slug}.txt"
    vocab_output.parent.mkdir(parents=True, exist_ok=True)
    script_output.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        vocab_input = tmp_path / "vocab_input.txt"
        vocab_input.write_text(build_vocab_input(selection.theme), encoding="utf-8")
        metrics = run_prompt_with_metrics(
            config.resolve_prompt("podcast_vocab"),
            vocab_input,
            vocab_output,
            models=config.models,
            timeout=config.llm_timeout_s,
            prompt_label="podcast_vocab",
        )
        log.info(
            "event=vocab_written theme=%s chars=%d wall_ms=%d",
            selection.theme_slug, vocab_output.stat().st_size, metrics.get("wall_ms", 0),
        )

        vocab_text = vocab_output.read_text(encoding="utf-8")
        script_input = tmp_path / "script_input.txt"
        script_input.write_text(
            build_script_input(selection, vocab_text), encoding="utf-8"
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

    return selection, vocab_output, script_output


if __name__ == "__main__":
    service_manager = ServiceManager()
    config = PipelineConfig.from_files_and_args()
    try:
        generate_podcast(config, service_manager=service_manager)
    finally:
        if config.llm_provider == "local":
            service_manager.stop("llm")
