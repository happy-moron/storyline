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


def _parse_entries(response_text: str) -> list[list[str]]:
    blocks = re.split(r'\n\s*\n', response_text.strip())

    entries: list[list[str]] = []
    for block in blocks:
        lines = [ln.strip() for ln in block.split('\n') if ln.strip()]
        if not lines:
            continue
        entries.append(lines)

    if len(entries) != FLASHCARD_ENTRY_COUNT:
        raise VocabExtractionError(
            f"Expected {FLASHCARD_ENTRY_COUNT} vocab entries, got {len(entries)}"
        )

    for i, lines in enumerate(entries):
        if len(lines) != FLASHCARD_LINES_PER_ENTRY:
            raise VocabExtractionError(
                f"Entry {i+1} has {len(lines)} lines, expected {FLASHCARD_LINES_PER_ENTRY}"
            )

    return entries


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