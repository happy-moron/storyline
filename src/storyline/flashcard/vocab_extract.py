import re
import tempfile
from pathlib import Path

from storyline.logging import get_logger
from storyline.prompt_utils.run_prompt import run_prompt_with_metrics
from storyline.services.manager import ServiceManager

_log = get_logger("flashcard.vocab")

FLASHCARD_ENTRY_COUNT = 6
FLASHCARD_LINES_PER_ENTRY = 7
MAX_RETRIES = 3


class VocabExtractionError(Exception):
    pass


def _parse_entries(response_text: str, expected_count: int | None = None) -> list[list[str]]:
    if expected_count is None:
        expected_count = FLASHCARD_ENTRY_COUNT

    blocks = re.split(r'\n\s*\n', response_text.strip())

    entries: list[list[str]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split('\n') if ln.strip()]
        if not lines:
            continue
        entries.append(lines)

    if len(entries) != expected_count:
        raise VocabExtractionError(
            f"Expected {expected_count} vocab entries, got {len(entries)}"
        )

    for i, lines in enumerate(entries):
        if len(lines) != FLASHCARD_LINES_PER_ENTRY:
            raise VocabExtractionError(
                f"Entry {i+1} has {len(lines)} lines, expected {FLASHCARD_LINES_PER_ENTRY}"
            )

    return entries


def extract_vocab_from_list(
    words: list[str],
    prompt_template_path: str,
    service_manager: ServiceManager,
    models: list[str],
    timeout: int = 1500,
) -> list[list[str]]:
    """Extract flashcard entries for a list of words (1-{FLASHCARD_ENTRY_COUNT})."""
    word_count = len(words)
    if word_count < 1 or word_count > FLASHCARD_ENTRY_COUNT:
        raise VocabExtractionError(
            f"Word list must have 1-{FLASHCARD_ENTRY_COUNT} words, got {word_count}"
        )

    input_text = "\n".join(words)

    _log.info(
        "event=vocab_extract_list_start words=%d prompt=%s",
        word_count, prompt_template_path,
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        script_input = tmp_path / "word_list_input.txt"
        script_input.write_text(input_text, encoding="utf-8")

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            _log.info(
                "event=vocab_extract_list_attempt attempt=%d/%d words=%d",
                attempt, MAX_RETRIES, word_count,
            )

            try:
                response_path = tmp_path / f"vocab_response_{attempt}.txt"
                run_prompt_with_metrics(
                    prompt_template_path,
                    script_input,
                    response_path,
                    models=models,
                    timeout=timeout,
                    prompt_label="flashcard_vocab_list",
                )
                response_text = response_path.read_text(encoding="utf-8")

                entries = _parse_entries(response_text, expected_count=word_count)
                _log.info(
                    "event=vocab_extract_list_ok attempt=%d entries=%d",
                    attempt, len(entries),
                )
                return entries

            except VocabExtractionError as e:
                last_error = e
                _log.warning(
                    "event=vocab_extract_list_parse_error attempt=%d error=%s",
                    attempt, str(e),
                )
                if attempt < MAX_RETRIES:
                    continue

    raise VocabExtractionError(
        f"Vocab extraction from list failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )


def extract_vocab(
    script_path: Path,
    prompt_template_path: str,
    service_manager: ServiceManager,
    models: list[str],
    timeout: int = 1500,
) -> list[list[str]]:
    script_text = script_path.read_text(encoding="utf-8")

    _log.info(
        "event=vocab_extract_start script=%s chars=%d",
        script_path.name, len(script_text),
    )

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        script_input = tmp_path / "script_input.txt"
        script_input.write_text(script_text, encoding="utf-8")

        last_error = None
        for attempt in range(1, MAX_RETRIES + 1):
            _log.info("event=vocab_extract_attempt attempt=%d/%d", attempt, MAX_RETRIES)

            try:
                response_path = tmp_path / f"vocab_response_{attempt}.txt"
                run_prompt_with_metrics(
                    prompt_template_path,
                    script_input,
                    response_path,
                    models=models,
                    timeout=timeout,
                    prompt_label="flashcard_vocab",
                )
                response_text = response_path.read_text(encoding="utf-8")
                _log.info(
                    "event=vocab_response attempt=%d chars=%d",
                    attempt, len(response_text),
                )

                entries = _parse_entries(response_text)
                _log.info("event=vocab_extract_ok attempt=%d entries=%d", attempt, len(entries))
                return entries

            except VocabExtractionError as e:
                last_error = e
                _log.warning(
                    "event=vocab_parse_error attempt=%d error=%s",
                    attempt, str(e),
                )
                if attempt < MAX_RETRIES:
                    continue

    raise VocabExtractionError(
        f"Vocab extraction failed after {MAX_RETRIES} attempts. "
        f"Last error: {last_error}"
    )