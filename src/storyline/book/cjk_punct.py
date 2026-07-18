"""Strip and re-insert CJK punctuation for token-efficient POS annotation.

CJK punctuation accounts for ~10.6% of characters in Chinese POS input and
~14.7% of tokens in pipe-delimited output. The LLM doesn't need to see or
annotate it — punctuation is always tagged ``w`` and can be mechanically
re-inserted from the original source text.
"""

CJK_PUNCT = set(
    "\u0022\u0027"          # ASCII double/single quote
    "\u2018\u2019"            # curly single quotes
    "\u201c\u201d"            # curly double quotes
    "\u300c\u300d\u300e\u300f"  # CJK corner brackets
    "\uff02\uff07"            # fullwidth quotes
    "，。？！、：；（）《》【】…—～·"
)


def strip(text: str) -> tuple[str, list[tuple[int, str]]]:
    """Strip CJK punctuation from *text*, returning stripped text and positions.

    *positions* is a list of ``(char_index, punct_char)`` tuples recording
    where each punctuation character was in the stripped text.  Multiple
    punctuation characters at the same position are listed sequentially.
    """
    positions: list[tuple[int, str]] = []
    stripped: list[str] = []
    for ch in text:
        if ch in CJK_PUNCT:
            positions.append((len(stripped), ch))
        else:
            stripped.append(ch)
    return "".join(stripped), positions


def reinsert(
    tokens: list[list], positions: list[tuple[int, str]], original: str
) -> list[list]:
    """Re-insert punctuation tokens into a word-level token list.

    *tokens* — list of word-level ``[text, pinyin, pos, ...]`` entries (no punct).
    *positions* — from :func:`strip`, tells where each punct char belongs.
    *original* — the original text with punctuation (for validation).

    Returns a new token list with ``[punct_char, punct_char, "w"]`` tokens
    inserted at the correct positions.
    """
    # Strip CJK punctuation tokens that the LLM may have hallucinated
    # despite being told not to. These throw off the character-offset tracking.
    tokens = [t for t in tokens if not all(ch in CJK_PUNCT for ch in t[0])]

    result: list[list] = []
    pos_idx = 0
    char_offset = 0

    for token in tokens:
        text = token[0]
        remaining = text
        remaining_start = char_offset

        while pos_idx < len(positions):
            punct_pos, punct_char = positions[pos_idx]
            if punct_pos < remaining_start + len(remaining):
                rel = punct_pos - remaining_start
                if rel > 0:
                    result.append([remaining[:rel]] + token[1:])
                result.append([punct_char, punct_char, "w"])
                pos_idx += 1
                remaining = remaining[rel:]
                remaining_start = punct_pos
            else:
                break

        if remaining:
            result.append([remaining] + token[1:])
        char_offset += len(text)

    while pos_idx < len(positions):
        result.append([positions[pos_idx][1], positions[pos_idx][1], "w"])
        pos_idx += 1

    # Validate reconstruction matches original
    reconstructed = "".join(t[0] for t in result)

    def _norm(s: str) -> str:
        return "".join(s.split())

    if _norm(reconstructed) != _norm(original):
        raise ValueError(
            f"Punctuation re-insertion failed:\n"
            f"  Expected: {original!r}\n"
            f"  Got:      {reconstructed!r}"
        )

    return result


def format_compact(tokens: list[list]) -> str:
    """Serialize a list of token lists to the compact pipe-delimited format
    that :func:`storyline.book.parse_pipe_format.parse_tokenized_compact` can
    read back.

    >>> format_compact([["\u6211","w\u01d2","r"],["\u3002","\u3002","w"]])
    '\u6211|w\u01d2|r||\u3002|w'
    """
    parts: list[str] = []
    for token in tokens:
        if len(token) == 3 and token[2] == "w":
            # Punctuation: 2-field format (text|w, pinyin inferred)
            parts.append(f"{token[0]}|w")
        else:
            fields = [str(token[0]), str(token[1]), str(token[2])]
            parts.append("|".join(fields))
    return "||".join(parts)