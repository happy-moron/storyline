"""Parse and validate LLM breakdown output for the e-reader.

The breakdown prompt (prompts/breakdown.md) instructs the LLM to return
one entry per Chinese line in this format:

    <exact original chinese line>
    <blank line>
    <explanation point 1>
    <explanation point 2>
    ...

    <exact original chinese line>
    ...

This module parses that output and validates that every source line
is covered with at least one explanation point.
"""

import re
import unicodedata
from pathlib import Path


def _normalize(text: str) -> str:
    """NFKC-normalize and strip whitespace for comparison."""
    return unicodedata.normalize("NFKC", text.strip())


def _is_likely_header(line: str, source_lines: list[str]) -> int | None:
    """Check if *line* matches any (unused) source line — if so return its index.

    Uses NFKC-normalized comparison and a relaxed CJK-only fallback.
    """
    norm = _normalize(line)
    for i, src in enumerate(source_lines):
        if _normalize(src) == norm:
            return i

    # Relaxed: strip everything but CJK ideographs and letters
    def _cjk_only(s: str) -> str:
        return "".join(ch for ch in s if unicodedata.category(ch).startswith(("Lo", "L", "N")))

    cjk_norm = _cjk_only(norm)
    for i, src in enumerate(source_lines):
        if _cjk_only(_normalize(src)) == cjk_norm:
            return i
    return None


def parse_breakdown_output(raw_text: str, source_lines: list[str]) -> list[list[str]]:
    """Parse LLM breakdown response into per-source-line lists of explanation points.

    The LLM output format interleaves header lines (exact Chinese source text)
    with explanation blocks.  This function pairs them correctly by checking
    each blank-line-separated block's first line against source_lines.

    Args:
        raw_text: The raw LLM output.
        source_lines: The original Chinese lines.

    Returns:
        A list of the same length as *source_lines*, each element is a
        list of explanation-point strings (empty if the line wasn't covered).
    """
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("Breakdown output is empty")

    # Strip markdown fences
    raw_text = re.sub(r"^```[^\n]*\n", "", raw_text)
    raw_text = re.sub(r"\n```\s*$", "", raw_text)
    raw_text = raw_text.strip()

    # Strip any leading "Your Input" or other meta-text that may have leaked
    # past the prompt instruction
    raw_text = re.sub(r"^#\s*Your Input\s*\n", "", raw_text)

    # Split into blocks by blank lines
    raw_blocks = re.split(r"\n\s*\n", raw_text)
    blocks = [b.strip() for b in raw_blocks if b.strip()]

    if not blocks:
        raise ValueError("No content blocks found in breakdown output")

    # Pair blocks: headers with their explanation blocks.
    # A "header" block is one whose first line matches a source line.
    # An explanation block follows its header.  We first identify all
    # header indices, then pair accordingly.
    block_is_header: list[bool] = []
    for block in blocks:
        lines = [l.strip() for l in block.split("\n") if l.strip()]
        first = lines[0] if lines else ""
        block_is_header.append(_is_likely_header(first, source_lines) is not None)

    # If the LLM put everything in one block (no blank lines between Chinese
    # line and explanations), or if every block is a header (no explanation
    # blocks), try splitting by source-line headers within the block.
    if len(blocks) == 1 or all(block_is_header):
        return _parse_single_block(blocks[0], source_lines) if len(blocks) == 1 else _parse_single_block("\n".join(blocks), source_lines)

    # Pair headers with following non-header blocks
    paired: list[tuple[int, list[str]]] = []
    used_indices: set[int] = set()
    i = 0
    while i < len(blocks):
        if block_is_header[i]:
            lines = [l.strip() for l in blocks[i].split("\n") if l.strip()]
            header_text = lines[0]
            # Any extra lines in the header block become part of the explanations
            extra_points = lines[1:] if len(lines) > 1 else []
            match_idx = _is_likely_header(header_text, source_lines)
            if match_idx is not None and match_idx not in used_indices:
                used_indices.add(match_idx)
                # Collect following non-header blocks as explanations
                points: list[str] = list(extra_points)
                j = i + 1
                while j < len(blocks) and not block_is_header[j]:
                    sub_lines = [l.strip() for l in blocks[j].split("\n") if l.strip()]
                    points.extend(sub_lines)
                    j += 1
                paired.append((match_idx, points))
                i = j
                continue
        i += 1

    # Fill gaps (unmatched lines get empty breakdown)
    result: list[list[str]] = [[] for _ in source_lines]
    for idx, points in paired:
        result[idx] = points
    return result


def _parse_single_block(block: str, source_lines: list[str]) -> list[list[str]]:
    """Fallback: parse a single block where the LLM didn't use blank lines
    between Chinese lines and explanations.  Try to find source-line headers
    within lines of the block.
    """
    lines = [l.strip() for l in block.split("\n") if l.strip()]
    result: list[list[str]] = [[] for _ in source_lines]
    used_indices: set[int] = set()

    i = 0
    while i < len(lines):
        match_idx = _is_likely_header(lines[i], source_lines)
        if match_idx is not None and match_idx not in used_indices:
            used_indices.add(match_idx)
            points: list[str] = []
            j = i + 1
            while j < len(lines):
                next_match = _is_likely_header(lines[j], source_lines)
                if next_match is not None and next_match not in used_indices:
                    break
                points.append(lines[j])
                j += 1
            result[match_idx] = points
            i = j
        else:
            i += 1
    return result


def validate_breakdown_output(
    breakdowns: list[list[str]],
    source_lines: list[str],
) -> list[str]:
    """Check that every source line has at least one breakdown point.

    Returns a list of error messages (empty if valid).
    """
    errors: list[str] = []
    for i, (points, source) in enumerate(zip(breakdowns, source_lines)):
        if not points:
            errors.append(f"Line {i} has no breakdown: '{source[:60]}'")
        for pi, point in enumerate(points):
            if _normalize(point) == _normalize(source):
                errors.append(
                    f"Line {i} point {pi} is identical to source: '{point[:60]}'"
                )
    return errors


# ---------------------------------------------------------------------------
# Convenience: run full pipeline for a chapter
# ---------------------------------------------------------------------------

def generate_breakdown_for_chapter(
    chinese_lines: list[str],
    slug: str,
    base_dir: Path,
    *,
    resolve_prompt,
    models: list[str],
    llm_timeout_s: int,
    llm_retries: int,
    extra_options: dict | None = None,
    prompt_key: str = "breakdown",
    fix_prompt_key: str = "breakdown_fix",
) -> list[list[str]]:
    """Generate breakdown for a chapter's Chinese lines via the LLM.

    Returns a list of breakdown-point lists, one per input line.
    """
    from storyline.logging import get_logger
    from storyline.prompt_utils.run_prompt import run_prompt_with_metrics

    log = get_logger("book.breakdown")
    chunks_dir = base_dir / "chunks"
    input_path = chunks_dir / f"{slug}_breakdown_input.txt"
    output_path = chunks_dir / f"{slug}_breakdown_output.txt"

    input_text = "\n".join(f"zh=1\n{line}" for line in chinese_lines)
    input_path.write_text(input_text, encoding="utf-8")

    for attempt in range(llm_retries):
        if attempt == 0:
            log.info(
                "event=breakdown_llm_call lines=%d attempt=%d",
                len(chinese_lines), attempt + 1,
            )
            _call_llm(
                resolve_prompt(prompt_key), input_path, output_path,
                models, llm_timeout_s, extra_options, "breakdown",
            )

        raw_output = output_path.read_text(encoding="utf-8")
        parsed = parse_breakdown_output(raw_output, chinese_lines)
        errors = validate_breakdown_output(parsed, chinese_lines)

        if not errors:
            input_path.unlink(missing_ok=True)
            output_path.unlink(missing_ok=True)
            log.info(
                "event=breakdown_valid lines=%d attempt=%d",
                len(chinese_lines), attempt + 1,
            )
            return parsed

        if attempt >= llm_retries - 1:
            break

        log.warning(
            "event=breakdown_validation_errors attempt=%d errors=%s",
            attempt + 1, "; ".join(errors[:5]),
        )

        fix_input_path = chunks_dir / f"{slug}_breakdown_fix_input_{attempt}.txt"
        fix_output_path = chunks_dir / f"{slug}_breakdown_fix_output_{attempt}.txt"

        fix_input_text = _build_fix_input(input_text, raw_output, errors)
        fix_input_path.write_text(fix_input_text, encoding="utf-8")

        _call_llm(
            resolve_prompt(fix_prompt_key), fix_input_path, fix_output_path,
            models, llm_timeout_s, extra_options, "breakdown_fix",
        )

        fix_output_text = fix_output_path.read_text(encoding="utf-8")
        if fix_output_text.strip():
            output_path.write_text(fix_output_text, encoding="utf-8")

        fix_input_path.unlink(missing_ok=True)
        fix_output_path.unlink(missing_ok=True)

    input_path.unlink(missing_ok=True)
    output_path.unlink(missing_ok=True)
    raise ValueError(
        f"Breakdown generation failed after {llm_retries} attempts: "
        f"{'; '.join(errors[:5])}"
    )


def _call_llm(
    prompt_path: str, input_path: Path, output_path: Path,
    models: list[str], timeout: int, extra_options: dict | None,
    label: str,
) -> dict:
    from storyline.prompt_utils.run_prompt import run_prompt_with_metrics
    return run_prompt_with_metrics(
        prompt_path, input_path, output_path,
        models=models, timeout=timeout,
        prompt_label=label, extra_options=extra_options,
    )


def _build_fix_input(
    original_input: str,
    bad_output: str,
    errors: list[str],
) -> str:
    lines = [
        "# Original Input",
        "",
        original_input.strip(),
        "",
        "# Previous Output (with errors)",
        "",
        bad_output.strip(),
        "",
        "# Validation Errors",
        "",
    ]
    for e in errors:
        lines.append(f"- {e}")
    lines.append("")
    return "\n".join(lines)