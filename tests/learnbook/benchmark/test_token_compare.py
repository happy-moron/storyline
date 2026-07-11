"""Tests for learnbook.benchmark.token_compare."""

import pytest
from learnbook.benchmark.token_compare import (
    compare,
    compare_files,
    ComparisonResult,
    GlobalMetrics,
    ComparisonMeta,
    _parse_text,
    _SentenceInfo,
    _lcs_dp,
    _lcs_align,
    _levenshtein,
    _concat_pinyin,
    _compare_pinyin,
    _compare_pos,
    _build_char_pos_sequence,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _lines(*lines: str) -> str:
    return "\n".join(lines)


# Sentinel tokenization with single-character tokens for deterministic
# sentence keys (avoids key-collision edge cases).
S1 = "我|wǒ|r"    # token ①
S2 = "是|shì|v"    # token ②
S3 = "你|nǐ|r"    # token ③
S4 = "好|hǎo|a"    # token ④
S5 = "吗|ma|y"    # token ⑤
S6 = "很|hěn|d"    # token ⑥


# ---------------------------------------------------------------------------
# LCS unit tests
# ---------------------------------------------------------------------------

class TestLCS:
    def test_empty_sequences(self):
        dp = _lcs_dp([], [])
        assert dp == [[0]]

    def test_one_empty(self):
        dp = _lcs_dp(["a"], [])
        assert dp == [[0], [0]]

    def test_identical(self):
        dp = _lcs_dp(["a", "b", "c"], ["a", "b", "c"])
        assert dp[-1][-1] == 3

    def test_no_match(self):
        dp = _lcs_dp(["a", "b"], ["c", "d"])
        assert dp[-1][-1] == 0

    def test_partial_match(self):
        dp = _lcs_dp(["a", "b", "c", "d"], ["b", "d", "e"])
        assert dp[-1][-1] == 2

    def test_align_empty(self):
        result = _lcs_align([[0]], [], [])
        assert result == []

    def test_align_all_match(self):
        dp = _lcs_dp(["a", "b"], ["a", "b"])
        result = _lcs_align(dp, ["a", "b"], ["a", "b"])
        assert result == [("match", 0, 0), ("match", 1, 1)]

    def test_align_all_missing(self):
        dp = _lcs_dp(["a", "b"], [])
        result = _lcs_align(dp, ["a", "b"], [])
        assert result == [("missing", 0, None), ("missing", 1, None)]

    def test_align_all_extra(self):
        dp = _lcs_dp([], ["a", "b"])
        result = _lcs_align(dp, [], ["a", "b"])
        assert result == [("extra", None, 0), ("extra", None, 1)]


# ---------------------------------------------------------------------------
# Scenario tests (matching design doc §5)
# ---------------------------------------------------------------------------

class TestBasicScenarios:
    """Scenario 1: Candidate identical to reference → zero errors."""

    def test_identical_returns_zero_errors(self):
        text = _lines(S1 + "||" + S2, S3 + "||" + S4)
        result = compare(text, text)
        m = result.global_metrics
        assert m.total_token_errors == 0
        assert m.token_error_rate == 0.0
        assert m.missing_sentences == 0
        assert m.extra_sentences == 0
        assert m.flawless_sentences == 2
        assert m.aligned_sentence_pairs == 2

    def test_identical_meta_correct(self):
        text = _lines(S1 + "||" + S2, S3 + "||" + S4)
        result = compare(text, text)
        assert result.meta.reference_sentences == 2
        assert result.meta.reference_tokens == 4

    def test_identical_per_sentence_comparisons(self):
        text = _lines(S1 + "||" + S2, S3 + "||" + S4)
        result = compare(text, text)
        assert len(result.sentence_comparisons) == 2
        for sc in result.sentence_comparisons:
            assert sc.type == "matched"
            assert sc.token_alignment.matched_count == 2
            assert sc.token_alignment.missing_count == 0
            assert sc.token_alignment.extra_count == 0


class TestMissingSentence:
    """Scenario 2: Candidate missing one whole sentence."""

    def test_missing_sentence_count(self):
        ref = _lines(S1 + "||" + S2, S3 + "||" + S4, S5 + "||" + S6)
        cand = _lines(S1 + "||" + S2, S5 + "||" + S6)
        result = compare(ref, cand)
        m = result.global_metrics
        assert m.missing_sentences == 1
        assert m.extra_sentences == 0
        assert m.aligned_sentence_pairs == 2

    def test_missing_sentence_token_errors(self):
        ref = _lines(S1 + "||" + S2, S3 + "||" + S4, S5 + "||" + S6)
        cand = _lines(S1 + "||" + S2, S5 + "||" + S6)
        result = compare(ref, cand)
        # missing sentence S3+S4 has 2 tokens
        assert result.global_metrics.total_token_errors == 2
        assert result.global_metrics.token_error_rate == 2 / 6

    def test_missing_sentence_sentence_comparison(self):
        ref = _lines(S1 + "||" + S2, S3 + "||" + S4, S5 + "||" + S6)
        cand = _lines(S1 + "||" + S2, S5 + "||" + S6)
        result = compare(ref, cand)
        types = [sc.type for sc in result.sentence_comparisons]
        assert types == ["matched", "missing", "matched"]
        # The missing sentence should have ref_tokens but no cand_tokens
        missing = result.sentence_comparisons[1]
        assert missing.type == "missing"
        assert missing.ref_tokens == [S3, S4]
        assert missing.cand_tokens is None
        assert missing.ref_key == "你好"


class TestExtraSentence:
    """Scenario 3: Candidate has an extra sentence inserted."""

    def test_extra_sentence_count(self):
        ref = _lines(S1 + "||" + S2, S5 + "||" + S6)
        cand = _lines(S1 + "||" + S2, S3 + "||" + S4, S5 + "||" + S6)
        result = compare(ref, cand)
        m = result.global_metrics
        assert m.missing_sentences == 0
        assert m.extra_sentences == 1
        assert m.aligned_sentence_pairs == 2

    def test_extra_sentence_token_errors(self):
        ref = _lines(S1 + "||" + S2, S5 + "||" + S6)
        cand = _lines(S1 + "||" + S2, S3 + "||" + S4, S5 + "||" + S6)
        result = compare(ref, cand)
        # extra sentence S3+S4 adds 2 tokens
        assert result.global_metrics.total_token_errors == 2

    def test_extra_sentence_comparison_type(self):
        ref = _lines(S1 + "||" + S2, S5 + "||" + S6)
        cand = _lines(S1 + "||" + S2, S3 + "||" + S4, S5 + "||" + S6)
        result = compare(ref, cand)
        types = [sc.type for sc in result.sentence_comparisons]
        assert types == ["matched", "extra", "matched"]


class TestTokenLevelErrors:
    """Scenario 4-6: Token-level errors within matched sentences."""

    def test_token_omitted(self):
        ref = _lines(S1 + "||" + S2 + "||" + S3)
        cand = _lines(S1 + "||" + S3)
        # Keys: ref="我是你", cand="我你" → different keys → sentence mismatch
        result = compare(ref, cand)
        # Missing sentence (3 tokens) + extra sentence (2 tokens)
        assert result.global_metrics.total_token_errors == 5

    def test_token_added(self):
        ref = _lines(S1 + "||" + S3)
        cand = _lines(S1 + "||" + S2 + "||" + S3)
        result = compare(ref, cand)
        assert result.global_metrics.total_token_errors == 5

    def test_wrong_pos_in_matched_sentence(self):
        ref = _lines(S1 + "||" + "吃|chī|v")
        cand = _lines(S1 + "||" + "吃|chī|n")
        result = compare(ref, cand)
        # Same key "我吃" → sentences match, but token mismatch → 2 errors
        assert result.global_metrics.total_token_errors == 2
        assert result.global_metrics.missing_tokens == 1
        assert result.global_metrics.extra_tokens == 1
        assert result.global_metrics.missing_sentences == 0
        assert result.global_metrics.extra_sentences == 0

    def test_compound_missing_breakdown(self):
        ref = _lines("担心|dānxīn|v")
        cand = _lines("担心|dānxīn|v")
        result = compare(ref, cand)
        # Same key "担心" and same token → flawless
        assert result.global_metrics.total_token_errors == 0


class TestEdgeCases:
    """Scenarios 10-13: Edge cases."""

    def test_both_empty(self):
        result = compare("", "")
        m = result.global_metrics
        assert m.total_token_errors == 0
        assert m.token_error_rate == 0.0
        assert m.missing_sentences == 0
        assert m.extra_sentences == 0

    def test_ref_nonempty_cand_empty(self):
        ref = _lines(S1 + "||" + S2, S3 + "||" + S4)
        result = compare(ref, "")
        m = result.global_metrics
        assert m.missing_sentences == 2
        assert m.total_token_errors == 4
        assert m.token_error_rate == 1.0

    def test_cand_nonempty_ref_empty(self):
        cand = _lines(S1 + "||" + S2)
        result = compare("", cand)
        m = result.global_metrics
        assert m.extra_sentences == 1
        assert m.total_token_errors == 2
        # When reference has 0 tokens, error rate is 0
        assert m.token_error_rate == 0.0

    def test_whitespace_trimmed(self):
        result = compare(
            "  我|wǒ|r||是|shì|v  \n\n",
            "  \n我|wǒ|r||是|shì|v  ",
        )
        assert result.global_metrics.total_token_errors == 0

    def test_english_tokens(self):
        text = _lines("Avdyeitch|Avdyeitch|nr||说|shuō|v")
        result = compare(text, text)
        assert result.global_metrics.total_token_errors == 0

    def test_blank_lines_ignored(self):
        ref = _lines("", S1 + "||" + S2, "", S3 + "||" + S4, "")
        cand = _lines(S1 + "||" + S2, S3 + "||" + S4)
        result = compare(ref, cand)
        assert result.global_metrics.total_token_errors == 0
        assert result.meta.reference_sentences == 2


class TestDuplicateKeys:
    """Scenario 9: Duplicate sentences (same key) — LCS aligns greedily."""

    def test_duplicate_keys_aligned_greedily(self):
        ref = _lines(S1 + "||" + S2, S1 + "||" + S2)
        cand = _lines(S1 + "||" + S2, S1 + "||" + S2)
        result = compare(ref, cand)
        assert result.global_metrics.total_token_errors == 0
        assert result.global_metrics.aligned_sentence_pairs == 2

    def test_duplicate_keys_one_extra(self):
        ref = _lines(S1 + "||" + S2, S1 + "||" + S2)
        cand = _lines(S1 + "||" + S2, S1 + "||" + S2, S1 + "||" + S2)
        result = compare(ref, cand)
        assert result.global_metrics.extra_sentences == 1
        assert result.global_metrics.aligned_sentence_pairs == 2


class TestReSegmentation:
    """Scenario 8: Same Chinese text but different tokenization."""

    def test_resegmentation(self):
        # "别担心" as two tokens vs one token
        ref = _lines("别|bié|d||担心|dānxīn|v")
        cand = _lines("别担心|biédānxīn|v")
        # Same key "别担心" → sentences match
        result = compare(ref, cand)
        m = result.global_metrics
        assert m.aligned_sentence_pairs == 1
        assert m.missing_sentences == 0
        assert m.extra_sentences == 0
        # Token alignment: 0 matches, 2 missing, 1 extra = 3 errors
        assert m.total_token_errors == 3
        assert m.flawless_sentences == 0


class TestFileComparison:
    """Integration: compare_files using actual eval golden files."""

    def test_identical_golden_files(self):
        result = compare_files(
            "eval/block_01_golden_tokenization.txt",
            "eval/block_01_golden_tokenization.txt",
        )
        assert result.global_metrics.total_token_errors == 0

    def test_file_not_found(self):
        with pytest.raises(FileNotFoundError):
            compare_files("eval/nonexistent.txt", "eval/block_01_golden_tokenization.txt")

    def test_all_golden_files_self_compare(self):
        for i in [1, 2, 3, 4, 5]:
            path = f"eval/block_0{i}_golden_tokenization.txt"
            result = compare_files(path, path)
            assert result.global_metrics.total_token_errors == 0, f"block_0{i} had errors"


class TestParseText:
    def test_parse_simple(self):
        text = _lines(S1 + "||" + S2, S3 + "||" + S4)
        sentences = _parse_text(text)
        assert len(sentences) == 2
        assert sentences[0].key == "我是"
        assert sentences[0].tokens == [S1, S2]
        assert sentences[0].index == 0
        assert sentences[1].key == "你好"
        assert sentences[1].tokens == [S3, S4]
        assert sentences[1].index == 1

    def test_parse_empty(self):
        assert _parse_text("") == []
        assert _parse_text("\n\n") == []

    def test_parse_compound_tokens(self):
        text = _lines("担心|dānxīn|v")
        sentences = _parse_text(text)
        assert len(sentences) == 1
        assert sentences[0].key == "担心"
        assert len(sentences[0].tokens) == 1


class TestComparisonResultFields:
    def test_result_structure(self):
        text = _lines(S1 + "||" + S2)
        result = compare(text, text)
        assert isinstance(result, ComparisonResult)
        assert isinstance(result.meta, ComparisonMeta)
        assert isinstance(result.global_metrics, GlobalMetrics)
        assert isinstance(result.sentence_comparisons, list)
        assert len(result.sentence_comparisons) == 1

    def test_sentence_comparison_matched_structure(self):
        text = _lines(S1 + "||" + S2)
        result = compare(text, text)
        sc = result.sentence_comparisons[0]
        assert sc.type == "matched"
        assert sc.ref_index == 0
        assert sc.cand_index == 0
        assert sc.ref_key == "我是"
        assert sc.cand_key == "我是"
        assert sc.token_alignment is not None
        assert sc.token_alignment.matched_count == 2
        assert sc.token_alignment.details[0]["type"] == "match"
        assert sc.token_alignment.details[0]["ref_token"] == S1
        assert sc.token_alignment.details[0]["cand_token"] == S1

    def test_token_error_rate_on_empty_ref(self):
        result = compare("", _lines(S1 + "||" + S2))
        assert result.global_metrics.token_error_rate == 0.0

    def test_flawless_count(self):
        # 2 sentences, both flawless
        text = _lines(S1 + "||" + S2, S3 + "||" + S4)
        result = compare(text, text)
        assert result.global_metrics.flawless_sentences == 2

        # 2 sentences, one with POS error
        ref = _lines(S1 + "||" + S2, S3 + "||" + "吃|chī|v")
        cand = _lines(S1 + "||" + S2, S3 + "||" + "吃|chī|n")
        result = compare(ref, cand)
        assert result.global_metrics.flawless_sentences == 1


# ---------------------------------------------------------------------------
# Pinyin comparison tests
# ---------------------------------------------------------------------------

class TestLevenshtein:
    def test_identical(self):
        assert _levenshtein("abc", "abc") == 0

    def test_empty(self):
        assert _levenshtein("", "") == 0
        assert _levenshtein("abc", "") == 3
        assert _levenshtein("", "abc") == 3

    def test_substitution(self):
        assert _levenshtein("abc", "axc") == 1

    def test_insertion(self):
        assert _levenshtein("abc", "abxc") == 1

    def test_deletion(self):
        assert _levenshtein("abc", "ac") == 1


class TestConcatPinyin:
    def test_single_token_no_space(self):
        assert _concat_pinyin(["担心|dānxīn|v"]) == "dānxīn"

    def test_single_token_with_space(self):
        assert _concat_pinyin(["担心|dān xīn|v"]) == "dānxīn"

    def test_multiple_tokens(self):
        tokens = ["心|xīn|n", "里|lǐ|f"]
        assert _concat_pinyin(tokens) == "xīnlǐ"

    def test_multiple_tokens_spaces(self):
        tokens = ["心里|xīn lǐ|s"]
        assert _concat_pinyin(tokens) == "xīnlǐ"

    def test_mixed_spaces(self):
        tokens = ["玻璃|bō lí|n", "杯|bēi|n"]
        assert _concat_pinyin(tokens) == "bōlíbēi"

    def test_no_pinyin_field(self):
        assert _concat_pinyin(["nosep"]) == ""


class TestComparePinyin:
    def test_identical(self):
        errs, total = _compare_pinyin(
            ["我|wǒ|r", "是|shì|v"],
            ["我|wǒ|r", "是|shì|v"],
        )
        assert errs == 0
        assert total == 5  # wǒshì

    def test_spacing_difference_only(self):
        # Space-normalized, these should be identical
        errs, total = _compare_pinyin(
            ["担心|dānxīn|v"],
            ["担心|dān xīn|v"],
        )
        assert errs == 0

    def test_tone_mark_difference(self):
        # bōlibēi vs bōlíbēi — one character different (li vs lí)
        errs, total = _compare_pinyin(
            ["玻璃杯|bōlibēi|n"],
            ["玻璃|bōlí|n", "杯|bēi|n"],
        )
        assert errs == 1  # 'i' vs 'í'

    def test_resegmentation_no_pinyin_diff(self):
        # Same characters, different tokens, same pinyin
        errs, total = _compare_pinyin(
            ["吃|chī|v", "光|guāng|a"],
            ["吃光|chī guāng|v"],
        )
        assert errs == 0  # chīguāng matches after space removal


class TestComparePOS:
    def test_identical(self):
        matches, mismatches, total = _compare_pos(
            ["我|wǒ|r", "是|shì|v"],
            ["我|wǒ|r", "是|shì|v"],
        )
        assert matches == 2
        assert mismatches == 0
        assert total == 2

    def test_single_pos_mismatch(self):
        matches, mismatches, total = _compare_pos(
            ["我|wǒ|r", "吃|chī|v"],
            ["我|wǒ|r", "吃|chī|n"],
        )
        assert matches == 1
        assert mismatches == 1
        assert total == 2

    def test_resegmentation_same_pos(self):
        # 心里 as one token (POS=s) vs 心(POS=n) + 里(POS=f)
        # Per-char: [s, s] vs [n, f] → 2 mismatches
        matches, mismatches, total = _compare_pos(
            ["心里|xīn lǐ|s"],
            ["心|xīn|n", "里|lǐ|f"],
        )
        assert matches == 0
        assert mismatches == 2
        assert total == 2

    def test_resegmentation_matching_pos(self):
        # 玻璃杯 as one token (POS=n) vs 玻璃(POS=n) + 杯(POS=n)
        # Per-char: [n, n, n] vs [n, n, n] → all match
        matches, mismatches, total = _compare_pos(
            ["玻璃杯|bō lí bēi|n"],
            ["玻璃|bō lí|n", "杯|bēi|n"],
        )
        assert matches == 3
        assert mismatches == 0
        assert total == 3

    def test_multi_char_token(self):
        # 不好意思 is 4 chars, POS=l
        matches, mismatches, total = _compare_pos(
            ["不好意思|bù hǎo yì si|l"],
            ["不好意思|bù hǎo yì si|a"],
        )
        assert matches == 0
        assert mismatches == 4
        assert total == 4


class TestBuildCharPOSSequence:
    def test_single_char_tokens(self):
        seq = _build_char_pos_sequence(["我|wǒ|r", "是|shì|v"])
        assert seq == ["r", "v"]

    def test_multi_char_token(self):
        seq = _build_char_pos_sequence(["心里|xīn lǐ|s"])
        assert seq == ["s", "s"]

    def test_mixed(self):
        seq = _build_char_pos_sequence(["我|wǒ|r", "心里|xīn lǐ|s", "的|de|u"])
        assert seq == ["r", "s", "s", "u"]

    def test_english_token(self):
        seq = _build_char_pos_sequence(["Avdyeitch|Avdyeitch|nr"])
        assert seq == ["nr"] * 9  # 9 letters


# ---------------------------------------------------------------------------
# Integration: pinyin & POS in compare()
# ---------------------------------------------------------------------------

class TestCompareWithPinyinPOS:
    def test_global_metrics_has_pinyin_pos_fields(self):
        text = _lines(S1 + "||" + S2)
        result = compare(text, text)
        m = result.global_metrics
        assert m.pinyin_errors == 0
        assert m.pinyin_total > 0
        assert m.pos_matches == 2
        assert m.pos_mismatches == 0
        assert m.pos_error_rate == 0.0

    def test_token_alignment_has_pinyin_pos_fields(self):
        text = _lines(S1 + "||" + S2)
        result = compare(text, text)
        ta = result.sentence_comparisons[0].token_alignment
        assert ta.pinyin_errors == 0
        assert ta.pinyin_total > 0
        assert ta.pos_matches == 2
        assert ta.pos_mismatches == 0

    def test_pinyin_error_aggregates(self):
        # One sentence with tone error
        ref = _lines("我|wǒ|r||是|shì|v", "吃|chī|v")
        cand = _lines("我|wǒ|r||是|shì|v", "吃|chì|v")  # chī → chì
        result = compare(ref, cand)
        m = result.global_metrics
        assert m.pinyin_errors == 1

    def test_pos_error_aggregates(self):
        ref = _lines("我|wǒ|r||是|shì|v")
        cand = _lines("我|wǒ|r||是|shì|n")  # v → n
        result = compare(ref, cand)
        m = result.global_metrics
        assert m.pos_mismatches == 1
        assert m.pos_matches == 1
        assert m.pos_error_rate == 0.5

    def test_only_matched_sentences_contribute(self):
        # Missing sentence should NOT contribute to pinyin/POS
        ref = _lines("我|wǒ|r||是|shì|v", "你|nǐ|r||好|hǎo|a")
        cand = _lines("我|wǒ|r||是|shì|v")
        result = compare(ref, cand)
        m = result.global_metrics
        # Only 1 matched sentence contributes
        assert m.pinyin_total > 0
        assert m.pinyin_errors == 0
        assert m.pos_total == 2

    def test_pinyin_spacing_calibration_scenario(self):
        # Simulate calibration: same text, different pinyin spacing
        ref = _lines("别|bié|d||担心|dānxīn|v||擦|cā|v||脚|jiǎo|n||的|de|u||事|shì|n")
        cand = _lines("别|bié|d||担心|dān xīn|v||擦|cā|v||脚|jiǎo|n||的|de|u||事|shì|n")
        result = compare(ref, cand)
        m = result.global_metrics
        # Token errors from segmentation (dānxīn vs dān xīn field mismatch)
        # But pinyin should be identical after space normalization
        assert m.pinyin_errors == 0
        assert m.token_error_rate > 0  # segmentation still counts as token errs