"""Parse LLM chunking output (@instruct/@previous/@next blocks).

The chunking prompt instructs the LLM to mark boundaries between
natural-reading chunks using anchor lines from the source text.
Each block defines where one chunk ends and the next begins.
"""

import re
import json
from pathlib import Path


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
        - @previous and @next within each block are adjacent in source
    """
    blocks = parse_chunk_blocks(text)

    line_to_idx: dict[str, int] = {}
    for i, line in enumerate(source_lines):
        line_to_idx.setdefault(line, i)

    def _find(text: str, label: str) -> int:
        if not text:
            return -1
        idx = line_to_idx.get(text)
        if idx is None:
            raise ValueError(f"{label} not found in source: {text!r}")
        return idx

    if blocks[0]["previous"]:
        raise ValueError("First @previous must be empty (start of text)")

    chunks: list[dict] = []
    chunk_start = _find(blocks[0]["next"], "First @next")

    for i, block in enumerate(blocks):
        next_idx = _find(block["next"], f"Block {i} @next")

        if i > 0:
            prev_idx = _find(block["previous"], f"Block {i} @previous")
            chunks.append({
                "instruct": blocks[i - 1]["instruct"],
                "line_range": [chunk_start, prev_idx],
            })
            chunk_start = next_idx

    chunks.append({
        "instruct": blocks[-1]["instruct"],
        "line_range": [chunk_start, len(source_lines) - 1],
    })

    for i, block in enumerate(blocks):
        prev_idx = _find(block["previous"], f"Block {i} @previous") if block["previous"] else -1
        next_idx = _find(block["next"], f"Block {i} @next")
        if prev_idx >= 0 and next_idx != prev_idx + 1:
            raise ValueError(
                f"Block {i}: @previous (line {prev_idx}) and @next (line {next_idx}) "
                f"are not adjacent in source"
            )

    return chunks


def save_chunks_json(chunks: list[dict], output_path: str | Path) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"chunks": chunks}, f, ensure_ascii=False, indent=2)


def load_chunks_json(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["chunks"]