"""Strip and re-insert CJK punctuation for token-efficient POS annotation.

CJK punctuation accounts for ~10.6% of characters in Chinese POS input and
~14.7% of tokens in pipe-delimited output. The LLM doesn't need to see or
annotate it — punctuation is always tagged ``w`` and can be mechanically
re-inserted from the original source text.
"""

CJK_PUNCT = set("，。？！、：；""''（）《》【】…—～·")


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
    result: list[list] = []
    pos_idx = 0
    char_offset = 0

    for token in tokens:
        text = token[0]
        token_len = len(text)

        while pos_idx < len(positions):
            punct_pos, punct_char = positions[pos_idx]
            if punct_pos < char_offset + token_len:
                result.append([punct_char, punct_char, "w"])
                pos_idx += 1
            else:
                break

        result.append(token)
        char_offset += token_len

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
    that :func:`learnbook.book.parse_pipe_format.parse_tokenized_compact` can
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