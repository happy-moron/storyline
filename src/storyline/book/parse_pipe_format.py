"""Parse LLM pipe-delimited output into the dict structures expected
by the rest of the pipeline.

The LLM emits compact pipe-delimited text to save tokens.  These functions
convert it to the format consumed by ``create_custom_dict.py``,
``audiobook_gen_base.py``, and the webapp ``index.html``.

Two tokenized formats are supported:
  - *compact* (one sentence per line, ``||`` token separator) — current
  - *legacy*  (one token per line, blank lines between sentences) — for old data
"""

import re
from pathlib import Path


CJK_PUNCT = set("，。？！、：；""''““（）《》【】…—～·")


def _normalize_for_comparison(text: str) -> str:
    """Normalize text for comparison: collapse whitespace and strip CJK
    punctuation so validation works whether or not the LLM includes punct."""
    text = "".join(text.split())
    return "".join(ch for ch in text if ch not in CJK_PUNCT)


def strip_fences(text: str) -> str:
    """Strip surrounding markdown code fences (with or without language tag)."""
    text = text.strip()
    text = re.sub(r"^```[^\n]*\n", "", text)
    text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Source pipe (chinese|||english) — unchanged format
# ---------------------------------------------------------------------------

def parse_source_pipe(text: str) -> list[dict]:
    """Parse ``chinese|||english`` pipe format into dict list.

    >>> parse_source_pipe("你好|||Hello\\n谢谢|||Thank you")
    [{'chinese': '你好', 'english': 'Hello'}, {'chinese': '谢谢', 'english': 'Thank you'}]
    """
    text = strip_fences(text)
    sentences: list[dict] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "|||" not in line:
            continue
        chinese, english = line.split("|||", 1)
        sentences.append({"chinese": chinese.strip(), "english": english.strip()})

    if not sentences:
        raise ValueError("No valid sentence pairs found in pipe output")
    return sentences


def parse_source_file(path: str | Path) -> list[dict]:
    """Read *path* and parse it as source pipe format."""
    with open(path, "r", encoding="utf-8") as f:
        return parse_source_pipe(f.read())


# ---------------------------------------------------------------------------
# Tokenized — compact format (current)
# ---------------------------------------------------------------------------

def parse_tokenized_compact(
    text: str, expected_chinese: list[str] | None = None
) -> list[dict]:
    """Parse compact tokenized output.

    One sentence per line.  Tokens separated by ``||``, fields by ``|``.
    Punctuation: 2 fields (``text|w``), pinyin inferred as *text*.
    Each token has exactly 3 fields: ``text|pinyin|POS``.

    >>> parse_tokenized_compact("我|wǒ|r||。|w\\n你|nǐ|r||好|hǎo|a")
    [{'t': [['我','wǒ','r'], ['。','。','w']]}, {'t': [['你','nǐ','r'], ['好','hǎo','a']]}]

    If *expected_chinese* is given, validates that reconstructed tokens
    match the expected text for each sentence.
    """
    text = strip_fences(text)
    sentences: list[dict] = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue

        tokens: list[list] = []
        for token_str in line.split("||"):
            token_str = token_str.strip()
            if not token_str:
                continue
            parts = token_str.split("|")
            if len(parts) == 2:
                # Punctuation: text doubles as pinyin
                tokens.append([parts[0], parts[0], parts[1]])
            elif len(parts) >= 3:
                token = [parts[0], parts[1], parts[2]]
                tokens.append(token)
            # else: skip malformed token

        if tokens:
            sentences.append({"t": tokens})

    if not sentences:
        raise ValueError("No valid token sequences found in pipe output")

    # Inline validation
    if expected_chinese is not None:
        if len(sentences) != len(expected_chinese):
            raise ValueError(
                f"Sentence count mismatch: expected {len(expected_chinese)}, "
                f"got {len(sentences)}"
            )
        for i, (sentence, expected) in enumerate(zip(sentences, expected_chinese)):
            reconstructed = "".join(t[0] for t in sentence["t"])
            # Normalize whitespace and CJK/Latin punctuation equivalents
            if _normalize_for_comparison(reconstructed) != _normalize_for_comparison(expected):
                raise ValueError(
                    f"Sentence {i}: reconstructed text does not match expected.\n"
                    f"  Expected:  {''.join(expected.split())}\n"
                    f"  Got:       {''.join(reconstructed.split())}"
                )

    return sentences


def parse_tokenized_file(
    path: str | Path, expected_chinese: list[str] | None = None
) -> list[dict]:
    """Read *path* and parse as compact tokenized format.

    Auto-detects the format: if any line contains ``||`` the compact
    parser is used, otherwise the legacy parser.
    """
    with open(path, "r", encoding="utf-8") as f:
        text = f.read()

    # Auto-detect: compact format uses || as token separator.
    # LLMs sometimes use | instead of ||, producing broken compact output.
    # Detect and repair: lines with 3+ | fields but no || are regrouped.
    if "||" in text:
        return parse_tokenized_compact(text, expected_chinese=expected_chinese)

    # Check for broken compact format (| used instead of || for token separation)
    lines = text.strip().split("\n")
    blank_count = sum(1 for line in lines if line.strip() == "")
    if blank_count == 0 and lines:
        # Legacy format requires blank lines between sentences;
        # if there are none, this is likely broken compact format.
        # Try to repair: split each line into groups of 3 | fields.
        repaired_lines: list[str] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            fields = line.split("|")
            if len(fields) >= 3 and len(fields) % 3 == 0:
                tokens = ["|".join(fields[i : i + 3]) for i in range(0, len(fields), 3)]
                repaired_lines.append("||".join(tokens))
            else:
                repaired_lines.append(line)
        repaired = "\n".join(repaired_lines)
        if "||" in repaired:
            return parse_tokenized_compact(repaired, expected_chinese=expected_chinese)

    return parse_tokenized_pipe(text)


# ---------------------------------------------------------------------------
# Tokenized — legacy format (one token per line, for old data)
# ---------------------------------------------------------------------------

def parse_tokenized_pipe(text: str) -> list[dict]:
    """Parse legacy tokenized format (one token per line, blank lines
    between sentences).

    >>> parse_tokenized_pipe("我|wǒ|r\\n。|w\\n\\n你|nǐ|r\\n好|hǎo|a\\n。|w")
    [{'t': [['我','wǒ','r'], ['。','。','w']]}, {'t': [['你','nǐ','r'], ['好','hǎo','a'], ['。','。','w']]}]
    """
    text = strip_fences(text)
    sentences: list[dict] = []
    current: list[list] = []

    for line in text.split("\n"):
        line = line.strip()
        if not line:
            if current:
                sentences.append({"t": current})
                current = []
            continue

        if "|" not in line:
            continue

        parts = line.split("|")
        if len(parts) == 2:
            token: list = [parts[0], parts[0], parts[1]]
        elif len(parts) >= 3:
            token = [parts[0], parts[1], parts[2]]
        else:
            continue

        current.append(token)

    if current:
        sentences.append({"t": current})

    if not sentences:
        raise ValueError("No valid token sequences found in pipe output")
    return sentences