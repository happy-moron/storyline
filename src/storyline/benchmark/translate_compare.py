"""Translation quality check using a remote LLM.

Aggregates all translated Chinese sentences from the translate phase,
sends them to a remote LLM via ``prompts/check_translation_blind.txt``,
and parses the error output.

Output format per error line::

    <sentence_number>:<grammar|style>:<description>
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from zsp_llm_client.prompt_runner import PromptRunner

from storyline.benchmark import TaskResult, TranslateValidation
from storyline.prompt_utils.clean_response import strip_markdown_fences, strip_think_tags

_log = logging.getLogger(__name__)

PROMPT_CHECK = "prompts/check_translation_blind.txt"

_ERROR_LINE_RE = re.compile(r"^(\d+):(grammar|style):(.+)$")


def aggregate_translations(results: list[TaskResult]) -> tuple[str, list[tuple[int, int]]]:
    """Concatenate translated Chinese sentences from all translate results.

    Returns (aggregated_text, block_ranges) where *block_ranges* is a list of
    ``(start_1based, end_1based)`` tuples indicating which global sentence
    numbers belong to each result.
    """
    all_lines: list[str] = []
    ranges: list[tuple[int, int]] = []

    for r in results:
        if r.task != "translate" or r.validation is None:
            ranges.append((0, 0))
            continue
        if not isinstance(r.validation, TranslateValidation):
            ranges.append((0, 0))
            continue
        if r.valid is False or r.response_chars == 0:
            ranges.append((0, 0))
            continue

        response_raw = getattr(r, "_response_raw", "")
        lines = [l.strip() for l in response_raw.strip().split("\n") if l.strip()]
        if not lines:
            ranges.append((0, 0))
            continue

        start = len(all_lines) + 1
        all_lines.extend(lines)
        end = len(all_lines)
        ranges.append((start, end))

    aggregated = "\n".join(all_lines)
    return aggregated, ranges


def call_translation_check(aggregated_text: str, models: list[str]) -> str:
    """Send aggregated Chinese text to the remote check LLM.

    Returns the raw response text.
    """
    runner = PromptRunner()
    response = runner.run(PROMPT_CHECK, aggregated_text, models=models)
    if response is None:
        raise RuntimeError("PromptRunner returned None for translation check")
    response = strip_think_tags(response)
    response = strip_markdown_fences(response)
    return response


def parse_check_response(response: str) -> list[str]:
    """Parse the check LLM response into a list of error strings.

    Only lines matching ``<num>:<grammar|style>:<description>`` are kept.
    """
    errors: list[str] = []
    for line in response.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if _ERROR_LINE_RE.match(line):
            errors.append(line)
    return errors


def compute_error_ratio(errors: list[str], total_sentences: int) -> float:
    """Compute error ratio: len(errors) / total_sentences."""
    if total_sentences == 0:
        return 0.0
    return len(errors) / total_sentences


def partition_errors_by_block(
    errors: list[str],
    block_ranges: list[tuple[int, int]],
) -> list[list[str]]:
    """Split parsed errors into per-block lists based on sentence ranges.

    Each error line starts with a global sentence number. Returns a list
    parallel to *block_ranges*.
    """
    result: list[list[str]] = [[] for _ in block_ranges]
    for err in errors:
        m = _ERROR_LINE_RE.match(err)
        if not m:
            continue
        sent_num = int(m.group(1))
        for idx, (start, end) in enumerate(block_ranges):
            if start <= sent_num <= end:
                result[idx].append(err)
                break
    return result