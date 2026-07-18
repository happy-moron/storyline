"""Parse LLM chunking output in <line-range>:<instruction> format.

The chunking prompt instructs the LLM to annotate line ranges that need
specific voice directions. Unannotated lines become neutral chunks.
"""

import re
import json
from pathlib import Path

from storyline.logging import get_logger

_log = get_logger("book.parse_chunk")

_LINE_RE = re.compile(r'^\s*(\d+)(?:-(\d+))?\s*[:：]\s*(.*)')


def _detect_lang(line: str) -> str:
    """Return 'zh' if fullwidth colon, 'en' if ASCII colon."""
    return 'zh' if '：' in line else 'en'


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```[^\n]*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def _parse_instructions(text: str) -> list[tuple[int, int, str, str]]:
    """Parse sparse instruction lines into (start_0based, end_0based, instr, lang)."""
    results = []
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        start = int(m.group(1)) - 1
        end_group = m.group(2)
        end = int(end_group) - 1 if end_group else start
        instr = m.group(3).strip()
        lang = _detect_lang(line)
        results.append((start, end, instr, lang))
    return results


def _pair_instructions(
    instructions: list[tuple[int, int, str, str]]
) -> list[tuple[int, int, str, str]]:
    """Pair consecutive English/Chinese instructions for the same line range.

    Returns (start, end, en_instr, zh_instr). Unpaired lines get an empty
    string for the missing language.
    """
    paired = []
    i = 0
    while i < len(instructions):
        start, end, instr, lang = instructions[i]
        en_instr = ""
        zh_instr = ""

        lookahead = instructions[i + 1] if i + 1 < len(instructions) else None
        if lookahead and lookahead[0] == start and lookahead[1] == end and lookahead[3] != lang:
            next_start, next_end, next_instr, next_lang = lookahead
            en_instr = instr if lang == 'en' else next_instr
            zh_instr = next_instr if lang == 'en' else instr
            i += 2
        else:
            if lang == 'zh':
                zh_instr = instr
            else:
                en_instr = instr
            i += 1

        paired.append((start, end, en_instr, zh_instr))
    return paired


def parse_chunk_format(text: str, source_lines: list[str]) -> list[dict]:
    """Parse LLM chunking output and produce contiguous chunks.

    *text* is the raw LLM response with <line-range>:<instruction> lines.
    *source_lines* the stripped source lines (one per sentence).

    Returns a list of chunks. Each chunk has:
        - ``instruct``: str — English voice direction (empty if none)
        - ``instruct_zh``: str — Chinese voice direction (empty if none)
        - ``line_range``: [start, end] — 0-based inclusive indices

    Unannotated lines are grouped into chunks with empty instructions.
    Overlapping annotations are merged.
    """
    text = _strip_fences(text)
    raw_instructions = _parse_instructions(text)

    n = len(source_lines)
    if n == 0:
        return []

    # Clamp and filter out-of-range instructions
    raw_instructions.sort()
    valid: list[tuple[int, int, str, str]] = []
    for start, end, instr, lang in raw_instructions:
        start = max(0, start)
        end = max(start, end)
        if start >= n:
            continue
        end = min(n - 1, end)
        valid.append((start, end, instr, lang))

    # Pair English/Chinese instructions for matching ranges
    paired = _pair_instructions(valid)

    if not paired:
        return [{"instruct": "", "instruct_zh": "", "line_range": [0, n - 1]}]

    # Merge overlapping or adjacent-same-instruct ranges
    merged: list[list] = []
    for start, end, en_instr, zh_instr in paired:
        if not merged:
            merged.append([start, end, en_instr, zh_instr])
            continue

        prev = merged[-1]
        if start <= prev[1]:
            prev[1] = max(prev[1], end)
            if en_instr and en_instr != prev[2]:
                prev[2] = f"{prev[2]}; {en_instr}" if prev[2] else en_instr
            if zh_instr and zh_instr != prev[3]:
                prev[3] = f"{prev[3]}; {zh_instr}" if prev[3] else zh_instr
        elif start == prev[1] + 1 and en_instr == prev[2] and zh_instr == prev[3]:
            prev[1] = end
        else:
            merged.append([start, end, en_instr, zh_instr])

    # Build chunks, filling gaps with empty instruct
    chunks: list[dict] = []
    pos = 0
    for start, end, en_instr, zh_instr in merged:
        if pos < start:
            chunks.append({"instruct": "", "instruct_zh": "", "line_range": [pos, start - 1]})
        chunks.append({"instruct": en_instr, "instruct_zh": zh_instr, "line_range": [start, end]})
        pos = end + 1

    if pos < n:
        chunks.append({"instruct": "", "instruct_zh": "", "line_range": [pos, n - 1]})

    # Safety-net clamping (covers edge cases from overlap merging)
    for ci, chunk in enumerate(chunks):
        s, e = chunk["line_range"]
        if s < 0:
            _log.warning("Chunk %d negative start %d — clamping to 0.", ci, s)
            chunk["line_range"][0] = 0
            s = 0
        if e < s:
            _log.warning("Chunk %d inverted range [%d, %d] — clamping.", ci, s, e)
            chunk["line_range"][1] = s
            e = s
        if s >= n:
            _log.warning("Chunk %d starts past text end (%d >= %d) — clamping.", ci, s, n)
            chunk["line_range"] = [n - 1, n - 1]

    if not chunks:
        chunks = [{"instruct": "", "instruct_zh": "", "line_range": [0, n - 1]}]

    return chunks


def save_chunks_json(chunks: list[dict], output_path: str | Path) -> None:
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"chunks": chunks}, f, ensure_ascii=False, indent=2)


def load_chunks_json(path: str | Path) -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data["chunks"]