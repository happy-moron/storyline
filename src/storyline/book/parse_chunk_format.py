"""Parse LLM chunking output (@instruct/@previous/@next blocks).

The chunking prompt instructs the LLM to mark boundaries between
natural-reading chunks using anchor lines from the source text.
Each block defines where one chunk ends and the next begins.
"""

import re
import json
import logging
from pathlib import Path

_log = logging.getLogger(__name__)


_BLOCK_SEP_RE = re.compile(r'(?=\n@instruct:)')
_BLOCK_RE = re.compile(
    r'@instruct:\s*(.*?)\n@previous:\s*(.*?)\n@next:\s*(.*?)$',
    re.DOTALL,
)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[^\n]*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def parse_chunk_blocks(text: str) -> list[dict]:
    """Parse raw @instruct/@previous/@next blocks into dicts.

    Returns a list where each dict has ``instruct``, ``previous``, ``next``.
    Raises ValueError if no blocks found.
    """
    text = _strip_fences(text)
    raw_blocks = _BLOCK_SEP_RE.split(text)

    blocks = []
    for raw in raw_blocks:
        raw = raw.strip()
        if not raw:
            continue
        m = _BLOCK_RE.match(raw)
        if not m:
            continue
        blocks.append({
            "instruct": m.group(1).strip(),
            "previous": m.group(2).strip(),
            "next": m.group(3).strip(),
        })

    if not blocks:
        raise ValueError("No @instruct blocks found in chunking output")

    return blocks


def _index_lines(source_lines: list[str]) -> dict[str, list[int]]:
    """Map each line text to a list of all positions where it occurs."""
    idx: dict[str, list[int]] = {}
    for i, line in enumerate(source_lines):
        idx.setdefault(line, []).append(i)
    return idx


def _find_first(indices: dict[str, list[int]], text: str, label: str) -> int:
    """Return the first occurrence index of *text*."""
    idxs = indices.get(text)
    if not idxs:
        raise ValueError(f"{label} not found in source: {text!r}")
    return idxs[0]


def _find_last(indices: dict[str, list[int]], text: str, label: str) -> int:
    """Return the last occurrence index of *text*."""
    idxs = indices.get(text)
    if not idxs:
        raise ValueError(f"{label} not found in source: {text!r}")
    return idxs[-1]


def _find_prev_for_next(
    indices: dict[str, list[int]],
    prev_text: str,
    next_text: str,
    label: str,
) -> tuple[int, int]:
    """Find the closest pair (prev_idx, next_idx) where prev_text occurs before
    next_text in source_lines.

    When lines repeat, this chooses the occurrence of ``prev_text`` that is
    closest to (and before) the earliest valid ``next_text`` — matching the
    intended chunk boundary even when anchor lines aren't unique.
    """
    prev_idxs = indices.get(prev_text, [])
    next_idxs = indices.get(next_text, [])

    if not prev_idxs or not next_idxs:
        raise ValueError(f"{label} not found in source")

    best_prev = -1
    best_next = -1
    best_dist = float("inf")

    for ni in next_idxs:
        for pi in reversed(prev_idxs):
            if pi < ni:
                dist = ni - pi
                if dist < best_dist:
                    best_dist = dist
                    best_prev = pi
                    best_next = ni
                break

    if best_prev < 0:
        raise ValueError(
            f"{label}: @previous {prev_text!r} must appear before "
            f"@next {next_text!r} in source"
        )
    return best_prev, best_next


def parse_chunk_format(text: str, source_lines: list[str]) -> list[dict]:
    """Parse chunking LLM output and map boundaries to source line indices.

    *text* is the raw LLM response with @instruct/@previous/@next blocks.
    *source_lines* are the stripped source lines (one per sentence).

    Returns a list of chunks. Each chunk has:
        - ``instruct``: str (empty string if no direction)
        - ``line_range``: [start, end] — 0-based inclusive indices

    Validates:
        - First @previous is empty (marks start of text)
        - All anchor lines exist in source_lines
        - @previous occurs before @next in the source for each block
    """
    blocks = parse_chunk_blocks(text)

    if blocks[0]["previous"]:
        raise ValueError("First @previous must be empty (start of text)")

    indices = _index_lines(source_lines)

    # Validate first block's @next exists in source (even though chunk 0
    # always starts at line 0 when @previous is empty).
    _find_first(indices, blocks[0]["next"], "First @next")

    # First chunk always starts at line 0 (first @previous is empty).
    chunk_start = 0
    chunks: list[dict] = []

    for i, block in enumerate(blocks):
        if i == 0:
            continue

        prev_text = block["previous"]
        next_text = block["next"]

        if next_text:
            prev_idx, next_idx = _find_prev_for_next(
                indices, prev_text, next_text, f"Block {i}"
            )
        else:
            # Last block: @next is empty (end of text).
            # The chunk after this boundary starts at the line following @previous.
            prev_idx = _find_last(indices, prev_text, f"Block {i} @previous")
            next_idx = prev_idx + 1

        chunks.append({
            "instruct": blocks[i - 1]["instruct"],
            "line_range": [chunk_start, prev_idx],
        })
        chunk_start = next_idx

    chunks.append({
        "instruct": blocks[-1]["instruct"],
        "line_range": [chunk_start, len(source_lines) - 1],
    })

    # Clamp any inverted or out-of-bounds ranges caused by overlapping LLM boundaries.
    for ci, chunk in enumerate(chunks):
        start, end = chunk["line_range"]
        if start < 0:
            _log.warning(
                "Chunk %d has negative start %d — clamping to 0.", ci, start
            )
            chunk["line_range"][0] = 0
            start = 0
        if end < start:
            _log.warning(
                "Chunk %d has inverted range [%d, %d] — clamping to [%d, %d]. "
                "LLM chunk boundaries may be overlapping.",
                ci, start, end, start, start,
            )
            chunk["line_range"][1] = start
            end = start
        if start > len(source_lines) - 1:
            _log.warning(
                "Chunk %d starts beyond text end (%d > %d) — clamping to end.",
                ci, start, len(source_lines) - 1,
            )
            chunk["line_range"] = [len(source_lines) - 1, len(source_lines) - 1]

    return chunks


def save_chunks_json(chunks: list[dict], output_path: str | Path) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"chunks": chunks}, f, ensure_ascii=False, indent=2)


def load_chunks_json(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["chunks"]