import pytest

from storyline.book.tokenization_repair import (
    ErrorType,
    TokenError,
    ValidationReport,
    validate_full,
    apply_fixes,
    VALID_POS,
)


# ---------------------------------------------------------------------------
# validate_full
# ---------------------------------------------------------------------------

def make_tok(text_pinyin_pos_triples):
    """Build a tokenized sentence from (text, pinyin, pos) triples."""
    return {"t": [[t, p, pos] for t, p, pos in text_pinyin_pos_triples]}


class TestValidateFull:
    def test_all_clean(self):
        source = ["你好", "世界"]
        tokenized = [
            make_tok([("你", "nǐ", "r"), ("好", "hǎo", "a")]),
            make_tok([("世", "shì", "n"), ("界", "jiè", "n")]),
        ]
        raw = ["你|nǐ|r||好|hǎo|a", "世|shì|n||界|jiè|n"]
        report = validate_full(source, tokenized, raw)
        assert report.is_clean
        assert report.good_indices == [0, 1]
        assert report.bad_indices == []

    def test_content_mismatch(self):
        source = ["你好"]
        tokenized = [make_tok([("你", "nǐ", "r"), ("吗", "ma", "y")])]
        raw = ["你|nǐ|r||吗|ma|y"]
        report = validate_full(source, tokenized, raw)
        assert not report.is_clean
        assert len(report.errors) == 1
        assert report.errors[0].error_type == ErrorType.CONTENT_MISMATCH
        assert report.errors[0].source_text == "你好"
        assert report.errors[0].tokenized_line == "你|nǐ|r||吗|ma|y"

    def test_missing_sentence(self):
        source = ["你好", "世界"]
        tokenized = [make_tok([("你", "nǐ", "r"), ("好", "hǎo", "a")])]
        raw = ["你|nǐ|r||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        assert report.has_structural_errors
        assert report.errors[0].error_type == ErrorType.MISSING
        assert report.errors[0].sentence_index == 1

    def test_extra_sentence(self):
        source = ["你好"]
        tokenized = [
            make_tok([("你", "nǐ", "r"), ("好", "hǎo", "a")]),
            make_tok([("世", "shì", "n"), ("界", "jiè", "n")]),
        ]
        raw = ["你|nǐ|r||好|hǎo|a", "世|shì|n||界|jiè|n"]
        report = validate_full(source, tokenized, raw)
        assert report.has_structural_errors
        assert report.errors[0].error_type == ErrorType.EXTRA
        assert report.errors[0].sentence_index == 1

    def test_bad_pos(self):
        source = ["你好"]
        tokenized = [make_tok([("你", "nǐ", "zzz_invalid"), ("好", "hǎo", "a")])]
        raw = ["你|nǐ|zzz_invalid||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        assert not report.is_clean
        pos_errors = [e for e in report.errors if e.error_type == ErrorType.BAD_POS]
        assert len(pos_errors) == 1
        assert "zzz_invalid" in pos_errors[0].detail

    def test_missing_pinyin(self):
        source = ["你好"]
        tokenized = [make_tok([("你", "", "r"), ("好", "hǎo", "a")])]
        raw = ["你||r||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        pinyin_errors = [e for e in report.errors if e.error_type == ErrorType.MISSING_PINYIN]
        assert len(pinyin_errors) == 1
        assert "你" in pinyin_errors[0].detail

    def test_english_token_skips_pinyin_check(self):
        source = ["Hello你好"]
        tokenized = [make_tok([("Hello", "", "nr"), ("你", "nǐ", "r"), ("好", "hǎo", "a")])]
        raw = ["Hello||nr||你|nǐ|r||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        assert report.is_clean

    def test_w_pos_tag_is_skipped(self):
        source = ["你好"]
        tokenized = [make_tok([("你", "nǐ", "r"), ("好", "hǎo", "a")])]
        raw = ["你|nǐ|r||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        assert report.is_clean

    def test_multiple_errors_per_sentence(self):
        source = ["你好"]
        tokenized = [make_tok([("你", "", "zzz"), ("好", "hǎo", "a")])]
        raw = ["你||zzz||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        # One BAD_POS, one MISSING_PINYIN — but content might match
        assert len(report.errors) >= 2

    def test_comma_separated_pos_tags(self):
        source = ["学习"]
        tokenized = [make_tok([("学习", "xuéxí", "v,n")])]
        raw = ["学习|xuéxí|v,n"]
        report = validate_full(source, tokenized, raw)
        assert report.is_clean

    def test_bad_pos_in_comma_list(self):
        source = ["学习"]
        tokenized = [make_tok([("学习", "xuéxí", "v,zzz")])]
        raw = ["学习|xuéxí|v,zzz"]
        report = validate_full(source, tokenized, raw)
        pos_errors = [e for e in report.errors if e.error_type == ErrorType.BAD_POS]
        assert len(pos_errors) == 1

    def test_normalization_ignores_whitespace(self):
        source = ["你 好"]
        tokenized = [make_tok([("你", "nǐ", "r"), ("好", "hǎo", "a")])]
        raw = ["你|nǐ|r||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        assert report.is_clean

    def test_cjk_punct_stripped_in_comparison(self):
        source = ["你好。"]
        tokenized = [make_tok([("你", "nǐ", "r"), ("好", "hǎo", "a")])]
        raw = ["你|nǐ|r||好|hǎo|a"]
        report = validate_full(source, tokenized, raw)
        assert report.is_clean


# ---------------------------------------------------------------------------
# apply_fixes
# ---------------------------------------------------------------------------

class TestApplyFixes:
    def test_single_fix(self):
        raw = ["bad|line", "good|line", "also|good"]
        fix_output = "fixed|line"
        result = apply_fixes(raw, fix_output, [0])
        assert result == ["fixed|line", "good|line", "also|good"]

    def test_multiple_fixes(self):
        raw = ["bad1", "good", "bad2"]
        fix_output = "fixed1\nfixed2"
        result = apply_fixes(raw, fix_output, [0, 2])
        assert result == ["fixed1", "good", "fixed2"]

    def test_skips_fence_lines(self):
        raw = ["bad", "good"]
        fix_output = "```\nfixed\n```"
        result = apply_fixes(raw, fix_output, [0])
        assert result == ["fixed", "good"]

    def test_pads_result_for_bad_index(self):
        raw = ["only_one"]
        fix_output = "fixed"
        result = apply_fixes(raw, fix_output, [2])
        assert len(result) == 3
        assert result[2] == "fixed"
        assert result[0] == "only_one"

    def test_bad_indices_out_of_order(self):
        raw = ["a", "b", "c"]
        fix_output = "fix_a\nfix_c"
        result = apply_fixes(raw, fix_output, [2, 0])
        assert result == ["fix_a", "b", "fix_c"]

    def test_line_count_mismatch_raises(self):
        raw = ["a", "b", "c"]
        fix_output = "only_one_line"
        with pytest.raises(ValueError, match="line count mismatch"):
            apply_fixes(raw, fix_output, [0, 2])


# ---------------------------------------------------------------------------
# ValidationReport properties
# ---------------------------------------------------------------------------

class TestValidationReport:
    def test_is_clean(self):
        assert ValidationReport().is_clean
        report = ValidationReport(errors=[TokenError(0, ErrorType.CONTENT_MISMATCH)])
        assert not report.is_clean

    def test_has_structural_errors(self):
        report = ValidationReport(errors=[TokenError(0, ErrorType.MISSING)])
        assert report.has_structural_errors

        report = ValidationReport(errors=[TokenError(0, ErrorType.CONTENT_MISMATCH)])
        assert not report.has_structural_errors