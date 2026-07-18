"""Two-phase validation of tokenized LLM output and targeted repair.

Phase 1 checks structural integrity (sentence counts match).
Phase 2 checks per-sentence content (character reconstruction, POS validity,
pinyin presence).

When errors are concentrated in a minority of sentences, a repair prompt
sends only the bad entries back to the LLM with fix instructions, avoiding
costly full regeneration.
"""

from dataclasses import dataclass, field
from enum import Enum, auto

from storyline.book.parse_pipe_format import _normalize_for_comparison


class ErrorType(Enum):
    MISSING = auto()
    EXTRA = auto()
    CONTENT_MISMATCH = auto()
    BAD_POS = auto()
    MISSING_PINYIN = auto()


VALID_POS = frozenset({
    "a", "ad", "ag", "b", "c", "d", "e", "f", "g", "h", "i", "j",
    "k", "l", "m", "n", "nr", "ns", "nt", "p", "q", "r", "s",
    "t", "u", "v", "x", "y", "w",
})

_ASCII_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
)


@dataclass
class TokenError:
    sentence_index: int
    error_type: ErrorType
    source_text: str | None = None
    tokenized_line: str | None = None
    detail: str = ""


@dataclass
class ValidationReport:
    errors: list[TokenError] = field(default_factory=list)
    good_indices: list[int] = field(default_factory=list)
    bad_indices: list[int] = field(default_factory=list)

    @property
    def is_clean(self) -> bool:
        return len(self.errors) == 0

    @property
    def has_structural_errors(self) -> bool:
        return any(
            e.error_type in (ErrorType.MISSING, ErrorType.EXTRA)
            for e in self.errors
        )


def validate_full(
    source_sentences: list[str],
    tokenized_sentences: list[dict],
    raw_lines: list[str],
) -> ValidationReport:
    """Run structural and content validation on tokenized output.

    Args:
        source_sentences: Chinese text strings (punctuation/whitespace stripped).
        tokenized_sentences: Parsed output from ``parse_tokenized_file`` (no validation).
        raw_lines: Raw pipe-format lines from the LLM output file.

    Returns:
        ``ValidationReport`` with detailed per-sentence error information.
    """
    report = ValidationReport()

    n_src = len(source_sentences)
    n_tok = len(tokenized_sentences)

    # ---------- Phase 1: structural ----------
    if n_src != n_tok:
        for i in range(n_src):
            if i >= n_tok:
                report.errors.append(TokenError(
                    i, ErrorType.MISSING,
                    source_text=source_sentences[i],
                    detail=f"Sentence {i} missing from tokenized output",
                ))
                report.bad_indices.append(i)
        for i in range(n_src, n_tok):
            reconstructed = "".join(t[0] for t in tokenized_sentences[i].get("t", []))
            report.errors.append(TokenError(
                i, ErrorType.EXTRA,
                tokenized_line=reconstructed,
                detail=f"Extra tokenized sentence at index {i}",
            ))
            report.bad_indices.append(i)
        return report

    # ---------- Phase 2: content ----------
    for i in range(n_src):
        expected = source_sentences[i]
        tokens = tokenized_sentences[i].get("t", [])
        reconstructed = "".join(t[0] for t in tokens)
        has_error = False

        # Character reconstruction
        if _normalize_for_comparison(reconstructed) != _normalize_for_comparison(expected):
            raw_line = raw_lines[i] if i < len(raw_lines) else reconstructed
            report.errors.append(TokenError(
                i, ErrorType.CONTENT_MISMATCH,
                source_text=expected,
                tokenized_line=raw_line,
                detail=f"Expected chars '{expected}', got '{reconstructed}'",
            ))
            has_error = True

        # POS validity and pinyin presence
        for j, token in enumerate(tokens):
            if len(token) >= 3:
                raw_pos = token[2]
                if raw_pos != "w":
                    for tag in raw_pos.split(","):
                        tag = tag.strip()
                        if tag and tag not in VALID_POS:
                            report.errors.append(TokenError(
                                i, ErrorType.BAD_POS,
                                source_text=expected,
                                detail=f"S{i} token {j} ('{token[0]}'): invalid POS '{tag}'",
                            ))
                            has_error = True

            if len(token) >= 2:
                text = token[0]
                pinyin = token[1]
                if text and _is_ascii_token(text):
                    continue
                if not pinyin:
                    report.errors.append(TokenError(
                        i, ErrorType.MISSING_PINYIN,
                        source_text=expected,
                        detail=f"S{i} token {j} ('{text}'): missing pinyin",
                    ))
                    has_error = True

        if not has_error:
            report.good_indices.append(i)
        else:
            report.bad_indices.append(i)

    return report


def _is_ascii_token(text: str) -> bool:
    return all(ch in _ASCII_CHARS for ch in text)


def apply_fixes(
    raw_lines: list[str],
    fix_output_text: str,
    bad_indices: list[int],
) -> list[str]:
    """Splice fixed lines from a fix-prompt response back into *raw_lines*.

    The fix prompt is given only the failing sentences (stripped Chinese),
    so its output lines map 1:1 to *bad_indices* in order.
    """
    fix_lines = [
        l.strip() for l in fix_output_text.strip().split("\n")
        if l.strip() and not l.strip().startswith("```")
    ]

    sorted_indices = sorted(bad_indices)
    if len(fix_lines) != len(sorted_indices):
        raise ValueError(
            f"Fix output line count mismatch: expected {len(sorted_indices)}, "
            f"got {len(fix_lines)}"
        )

    result = list(raw_lines)
    max_idx = max(sorted_indices) if sorted_indices else -1
    while len(result) <= max_idx:
        result.append("")

    for target_idx, fix_line in zip(sorted_indices, fix_lines):
        if target_idx < len(result):
            result[target_idx] = fix_line

    return result