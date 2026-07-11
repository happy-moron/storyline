"""Golden comparison for Chinese POS tokenization output.

Compares a candidate tokenization file against a golden reference in the
compact pipe-delimited format (tokens separated by ``||``, fields by ``|``).

Algorithm:
  - Parse both files into sentences (list of raw token strings).
  - Extract sentence keys (concatenated Chinese text of all tokens).
  - LCS-based sentence alignment on sentence keys.
  - For matched sentence pairs: LCS-based token alignment on raw token strings.
  - Count missing/extra sentences and tokens.

Design: plans/token_comparison_design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class GlobalMetrics:
    aligned_sentence_pairs: int
    missing_sentences: int
    extra_sentences: int
    content_mismatch_sentences: int
    flawless_sentences: int
    missing_tokens: int
    extra_tokens: int
    total_token_errors: int
    token_error_rate: float
    # Space-normalized pinyin comparison (across matched sentences)
    pinyin_errors: int = 0
    pinyin_total: int = 0
    # Per-character POS tag comparison (across matched sentences)
    pos_matches: int = 0
    pos_mismatches: int = 0
    pos_total: int = 0
    pos_error_rate: float = 0.0


@dataclass
class ComparisonMeta:
    reference_file: str
    candidate_file: str
    reference_sentences: int
    candidate_sentences: int
    reference_tokens: int
    candidate_tokens: int


@dataclass
class SentenceComparison:
    type: str  # "matched", "missing", "extra", "content_mismatch"
    ref_index: int | None = None
    cand_index: int | None = None
    ref_key: str | None = None
    cand_key: str | None = None
    # For missing/extra sentences
    ref_tokens: list[str] | None = None
    cand_tokens: list[str] | None = None
    # For matched sentences
    token_alignment: TokenAlignment | None = None


@dataclass
class TokenAlignment:
    matched_count: int
    missing_count: int
    extra_count: int
    details: list[dict[str, str | None]] = field(default_factory=list)
    # Per-sentence pinyin & POS metrics
    pinyin_errors: int = 0
    pinyin_total: int = 0
    pos_matches: int = 0
    pos_mismatches: int = 0
    pos_total: int = 0


@dataclass
class ComparisonResult:
    """Top-level result from :func:`compare_files` or :func:`compare`.

    The *metrics* attribute is a flat summary suitable for benchmark reports;
    *global_metrics* is always present.  The *meta* and *sentence_comparisons*
    provide full transparency for debugging and display.
    """
    meta: ComparisonMeta
    global_metrics: GlobalMetrics
    sentence_comparisons: list[SentenceComparison] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

@dataclass
class _SentenceInfo:
    index: int           # 0-based line number
    key: str             # concatenated Chinese text of all tokens
    tokens: list[str]    # raw token strings e.g. "别|bié|d"


def _parse_file(path: str | Path) -> list[_SentenceInfo]:
    """Parse a compact pipe-delimited tokenization file.

    One sentence per line, tokens separated by ``||``.
    Empty lines are ignored.  Leading/trailing whitespace on each line is stripped.

    Raises FileNotFoundError if the file doesn't exist.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    text = path.read_text(encoding="utf-8")
    sentences: list[_SentenceInfo] = []

    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        tokens = [t.strip() for t in stripped.split("||") if t.strip()]
        if not tokens:
            continue
        # Sentence key = concatenated first field (Chinese text) of all tokens
        key_chars: list[str] = []
        for token in tokens:
            if "|" in token:
                key_chars.append(token.split("|", 1)[0])
            else:
                key_chars.append(token)
        key = "".join(key_chars)
        sentences.append(_SentenceInfo(index=i, key=key, tokens=tokens))

    return sentences


# ---------------------------------------------------------------------------
# LCS utilities
# ---------------------------------------------------------------------------

def _lcs_dp(a: Sequence, b: Sequence) -> list[list[int]]:
    """Compute the LCS DP table for sequences *a* and *b*."""
    m, n = len(a), len(b)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if a[i - 1] == b[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp


def _lcs_align(dp: list[list[int]], a: Sequence, b: Sequence) -> list[tuple[str, int | None, int | None]]:
    """Backtrack the LCS DP table, returning alignment decisions.

    Returns a list of ``(action, idx_a, idx_b)`` where *action* is one of
    ``"match"``, ``"missing"`` (in *a* only), or ``"extra"`` (in *b* only).
    Indices are 0-based into *a* / *b*.  The list is ordered from start to end.
    """
    aligned: list[tuple[str, int | None, int | None]] = []
    i, j = len(a), len(b)
    while i > 0 or j > 0:
        if i > 0 and j > 0 and a[i - 1] == b[j - 1]:
            aligned.append(("match", i - 1, j - 1))
            i -= 1
            j -= 1
        elif j > 0 and (i == 0 or dp[i][j - 1] >= dp[i - 1][j]):
            aligned.append(("extra", None, j - 1))
            j -= 1
        else:
            aligned.append(("missing", i - 1, None))
            i -= 1
    aligned.reverse()
    return aligned


# ---------------------------------------------------------------------------
# Per-sentence pinyin & POS comparison
# ---------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein (edit) distance between two strings."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if len(b) == 0:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr = [i + 1]
        for j, cb in enumerate(b):
            cost = 0 if ca == cb else 1
            curr.append(min(curr[-1] + 1, prev[j + 1] + 1, prev[j] + cost))
        prev = curr
    return prev[-1]


def _concat_pinyin(tokens: list[str]) -> str:
    """Concatenate pinyin from tokens into one string with spaces removed.

    For tokens like ``心里|xīn lǐ|s`` or ``心里|xīnlǐ|n``, extract the
    pinyin field (second ``|``-delimited field) and strip spaces.
    """
    parts: list[str] = []
    for token in tokens:
        fields = token.split("|")
        if len(fields) >= 2:
            parts.append(fields[1])
    return "".join(parts).replace(" ", "")


def _compare_pinyin(
    ref_tokens: list[str], cand_tokens: list[str]
) -> tuple[int, int]:
    """Compare space-normalized pinyin between reference and candidate tokens.

    Returns ``(edit_distance, reference_pinyin_length)``.
    """
    ref_py = _concat_pinyin(ref_tokens)
    cand_py = _concat_pinyin(cand_tokens)
    dist = _levenshtein(ref_py, cand_py)
    return dist, len(ref_py)


def _compare_pos(
    ref_tokens: list[str], cand_tokens: list[str]
) -> tuple[int, int, int]:
    """Compare POS tags character-by-character between reference and candidate.

    Each token's POS is replicated once per Chinese character in that token.
    Returns ``(matches, mismatches, total)``.
    """
    ref_pos = _build_char_pos_sequence(ref_tokens)
    cand_pos = _build_char_pos_sequence(cand_tokens)

    total = len(ref_pos)
    if len(cand_pos) != total:
        # Should not happen for matched sentences, but be defensive.
        matches = sum(1 for rp, cp in zip(ref_pos, cand_pos) if rp == cp)
        mismatches = max(total, len(cand_pos)) - matches
        return matches, mismatches, max(total, len(cand_pos))

    matches = sum(1 for rp, cp in zip(ref_pos, cand_pos) if rp == cp)
    mismatches = total - matches
    return matches, mismatches, total


def _build_char_pos_sequence(tokens: list[str]) -> list[str]:
    """Build a per-character POS sequence from tokens.

    For each token, replicate its POS tag once per Chinese character.
    Non-Chinese characters (letters, digits) count as one character each.

    Example: ``["心里|xīn lǐ|s"]`` → ``["s", "s"]``
             ``["心|xīn|n", "里|lǐ|f"]`` → ``["n", "f"]``
    """
    seq: list[str] = []
    for token in tokens:
        fields = token.split("|")
        if len(fields) < 3:
            # Malformed token — skip but don't crash.
            continue
        chars = fields[0]  # Chinese text
        pos = fields[2]    # POS tag
        seq.extend([pos] * len(chars))
    return seq


# ---------------------------------------------------------------------------
# Content-level mismatch detection
# ---------------------------------------------------------------------------

def _detect_content_mismatches(
    missing: list[_SentenceInfo],
    extra: list[_SentenceInfo],
    *,
    threshold: float = 0.2,
) -> tuple[list[tuple[_SentenceInfo, _SentenceInfo, float]], list[_SentenceInfo], list[_SentenceInfo]]:
    """Pair missing/extra sentences whose keys are near-matches (content-level diffs).

    Uses greedy closest-first pairing by edit ratio to avoid double-matching.
    Returns ``(pairs, remaining_missing, remaining_extra)`` where each pair
    is ``(ref_sentence, cand_sentence, edit_ratio)``.
    """
    candidates: list[tuple[float, int, int]] = []
    for mi, ms in enumerate(missing):
        for ei, es in enumerate(extra):
            dist = _levenshtein(ms.key, es.key)
            max_len = max(len(ms.key), len(es.key))
            ratio = dist / max_len if max_len > 0 else 0.0
            if ratio < threshold:
                candidates.append((ratio, mi, ei))

    candidates.sort(key=lambda x: x[0])

    used_missing: set[int] = set()
    used_extra: set[int] = set()
    pairs: list[tuple[_SentenceInfo, _SentenceInfo, float]] = []

    for ratio, mi, ei in candidates:
        if mi not in used_missing and ei not in used_extra:
            pairs.append((missing[mi], extra[ei], ratio))
            used_missing.add(mi)
            used_extra.add(ei)

    remaining_missing = [s for i, s in enumerate(missing) if i not in used_missing]
    remaining_extra = [s for i, s in enumerate(extra) if i not in used_extra]
    return pairs, remaining_missing, remaining_extra


# ---------------------------------------------------------------------------
# Core comparison
# ---------------------------------------------------------------------------

def compare(
    reference_text: str,
    candidate_text: str,
    *,
    reference_label: str = "reference",
    candidate_label: str = "candidate",
    content_mismatch_threshold: float = 0.2,
) -> ComparisonResult:
    """Compare candidate tokenization against a reference, both given as raw text.

    See :func:`compare_files` for file-based usage.
    """
    ref_sentences = _parse_text(reference_text)
    cand_sentences = _parse_text(candidate_text)

    return _do_compare(
        ref_sentences, cand_sentences, reference_label, candidate_label,
        content_mismatch_threshold=content_mismatch_threshold,
    )


def compare_files(
    reference_path: str | Path,
    candidate_path: str | Path,
    *,
    content_mismatch_threshold: float = 0.2,
) -> ComparisonResult:
    """Compare candidate tokenization file against a golden reference.

    Both files must be in compact pipe-delimited format.
    """
    ref_sentences = _parse_file(reference_path)
    cand_sentences = _parse_file(candidate_path)

    return _do_compare(
        ref_sentences,
        cand_sentences,
        str(reference_path),
        str(candidate_path),
        content_mismatch_threshold=content_mismatch_threshold,
    )


def _parse_text(text: str) -> list[_SentenceInfo]:
    """Parse raw compact pipe text into sentence infos (for testing)."""
    sentences: list[_SentenceInfo] = []
    for i, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if not stripped:
            continue
        tokens = [t.strip() for t in stripped.split("||") if t.strip()]
        if not tokens:
            continue
        key_chars: list[str] = []
        for token in tokens:
            if "|" in token:
                key_chars.append(token.split("|", 1)[0])
            else:
                key_chars.append(token)
        key = "".join(key_chars)
        sentences.append(_SentenceInfo(index=i, key=key, tokens=tokens))
    return sentences


def _do_compare(
    ref_sentences: list[_SentenceInfo],
    cand_sentences: list[_SentenceInfo],
    reference_label: str,
    candidate_label: str,
    *,
    content_mismatch_threshold: float = 0.2,
) -> ComparisonResult:
    ref_keys = [s.key for s in ref_sentences]
    cand_keys = [s.key for s in cand_sentences]

    ref_total_tokens = sum(len(s.tokens) for s in ref_sentences)
    cand_total_tokens = sum(len(s.tokens) for s in cand_sentences)

    # --- Sentence-level alignment ---
    dp_sent = _lcs_dp(ref_keys, cand_keys)
    sent_align = _lcs_align(dp_sent, ref_keys, cand_keys)

    # --- Content mismatch detection on unmatched sentences ---
    missing_sents = []
    extra_sents = []
    for action, ref_i, cand_i in sent_align:
        if action == "missing":
            missing_sents.append(ref_sentences[ref_i])
        elif action == "extra":
            extra_sents.append(cand_sentences[cand_i])

    content_pairs, _, _ = _detect_content_mismatches(
        missing_sents, extra_sents,
        threshold=content_mismatch_threshold,
    )
    content_pair_by_ref_key = {ref.key: (cand, ratio) for ref, cand, ratio in content_pairs}
    content_pair_cand_keys = {cand.key for _, cand, _ in content_pairs}

    comparisons: list[SentenceComparison] = []
    missing_sentences = 0
    extra_sentences = 0
    content_mismatch_sentences = 0
    aligned_pairs = 0
    flawless_pairs = 0
    missing_tokens_total = 0
    extra_tokens_total = 0

    for action, ref_i, cand_i in sent_align:
        if action == "match":
            ref_s = ref_sentences[ref_i]
            cand_s = cand_sentences[cand_i]
            aligned_pairs += 1

            # --- Token-level alignment ---
            dp_tok = _lcs_dp(ref_s.tokens, cand_s.tokens)
            tok_align = _lcs_align(dp_tok, ref_s.tokens, cand_s.tokens)

            matched_count = 0
            miss_count = 0
            extra_count = 0
            details: list[dict[str, str | None]] = []

            for tok_action, rt_i, ct_i in tok_align:
                if tok_action == "match":
                    matched_count += 1
                    details.append({
                        "type": "match",
                        "ref_token": ref_s.tokens[rt_i],
                        "cand_token": cand_s.tokens[ct_i],
                    })
                elif tok_action == "missing":
                    miss_count += 1
                    details.append({
                        "type": "missing",
                        "ref_token": ref_s.tokens[rt_i],
                        "cand_token": None,
                    })
                else:
                    extra_count += 1
                    details.append({
                        "type": "extra",
                        "ref_token": None,
                        "cand_token": cand_s.tokens[ct_i],
                    })

            missing_tokens_total += miss_count
            extra_tokens_total += extra_count

            is_flawless = miss_count == 0 and extra_count == 0
            if is_flawless:
                flawless_pairs += 1

            pinyin_errs, pinyin_tot = _compare_pinyin(ref_s.tokens, cand_s.tokens)
            pos_match, pos_mis, pos_tot = _compare_pos(ref_s.tokens, cand_s.tokens)

            comparisons.append(SentenceComparison(
                type="matched",
                ref_index=ref_s.index,
                cand_index=cand_s.index,
                ref_key=ref_s.key,
                cand_key=cand_s.key,
                token_alignment=TokenAlignment(
                    matched_count=matched_count,
                    missing_count=miss_count,
                    extra_count=extra_count,
                    details=details,
                    pinyin_errors=pinyin_errs,
                    pinyin_total=pinyin_tot,
                    pos_matches=pos_match,
                    pos_mismatches=pos_mis,
                    pos_total=pos_tot,
                ),
            ))

        elif action == "missing":
            ref_s = ref_sentences[ref_i]
            if ref_s.key in content_pair_by_ref_key:
                cand_s, _ratio = content_pair_by_ref_key[ref_s.key]
                content_mismatch_sentences += 1
                comparisons.append(SentenceComparison(
                    type="content_mismatch",
                    ref_index=ref_s.index,
                    cand_index=cand_s.index,
                    ref_key=ref_s.key,
                    cand_key=cand_s.key,
                ))
            else:
                missing_sentences += 1
                missing_tokens_total += len(ref_s.tokens)
                comparisons.append(SentenceComparison(
                    type="missing",
                    ref_index=ref_s.index,
                    ref_key=ref_s.key,
                    ref_tokens=ref_s.tokens,
                ))

        else:  # extra
            cand_s = cand_sentences[cand_i]
            if cand_s.key in content_pair_cand_keys:
                continue  # already represented in a content_mismatch entry
            extra_sentences += 1
            extra_tokens_total += len(cand_s.tokens)
            comparisons.append(SentenceComparison(
                type="extra",
                cand_index=cand_s.index,
                cand_key=cand_s.key,
                cand_tokens=cand_s.tokens,
            ))

    total_token_errors = missing_tokens_total + extra_tokens_total
    token_error_rate = (
        total_token_errors / ref_total_tokens if ref_total_tokens > 0 else 0.0
    )

    # Aggregate pinyin & POS across matched sentences
    pinyin_errors_total = 0
    pinyin_chars_total = 0
    pos_matches_total = 0
    pos_mismatches_total = 0
    pos_chars_total = 0
    for sc in comparisons:
        if sc.type == "matched" and sc.token_alignment is not None:
            pinyin_errors_total += sc.token_alignment.pinyin_errors
            pinyin_chars_total += sc.token_alignment.pinyin_total
            pos_matches_total += sc.token_alignment.pos_matches
            pos_mismatches_total += sc.token_alignment.pos_mismatches
            pos_chars_total += sc.token_alignment.pos_total

    pos_error_rate = (
        pos_mismatches_total / pos_chars_total if pos_chars_total > 0 else 0.0
    )

    return ComparisonResult(
        meta=ComparisonMeta(
            reference_file=reference_label,
            candidate_file=candidate_label,
            reference_sentences=len(ref_sentences),
            candidate_sentences=len(cand_sentences),
            reference_tokens=ref_total_tokens,
            candidate_tokens=cand_total_tokens,
        ),
        global_metrics=GlobalMetrics(
            aligned_sentence_pairs=aligned_pairs,
            missing_sentences=missing_sentences,
            extra_sentences=extra_sentences,
            content_mismatch_sentences=content_mismatch_sentences,
            flawless_sentences=flawless_pairs,
            missing_tokens=missing_tokens_total,
            extra_tokens=extra_tokens_total,
            total_token_errors=total_token_errors,
            token_error_rate=token_error_rate,
            pinyin_errors=pinyin_errors_total,
            pinyin_total=pinyin_chars_total,
            pos_matches=pos_matches_total,
            pos_mismatches=pos_mismatches_total,
            pos_total=pos_chars_total,
            pos_error_rate=pos_error_rate,
        ),
        sentence_comparisons=comparisons,
    )