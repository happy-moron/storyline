"""Tests for the pipe-format parser that converts LLM output to pipeline dicts."""

import pytest

from storyline.book.parse_pipe_format import (
    parse_source_pipe,
    parse_tokenized_compact,
    parse_tokenized_file,
    parse_tokenized_pipe,
)


class TestParseSourcePipe:
    def test_basic(self):
        result = parse_source_pipe("\u4f60\u597d|||Hello\n\u8c22\u8c22|||Thank you")
        assert result == [
            {"chinese": "\u4f60\u597d", "english": "Hello"},
            {"chinese": "\u8c22\u8c22", "english": "Thank you"},
        ]

    def test_fence_stripping(self):
        result = parse_source_pipe("```\n\u4f60\u597d|||Hello\n```")
        assert result == [{"chinese": "\u4f60\u597d", "english": "Hello"}]

    def test_trailing_newlines(self):
        result = parse_source_pipe("\u4f60\u597d|||Hello\n\n\n\u8c22\u8c22|||Thank you\n")
        assert len(result) == 2

    def test_empty_input_raises(self):
        try:
            parse_source_pipe("\n\n")
            assert False, "should have raised"
        except ValueError:
            pass


class TestParseTokenizedPipe:
    def test_basic(self):
        result = parse_tokenized_pipe("\u6211|w\u01d2|r\n\u7684|de|u\n\u3002|w")
        assert result == [
            {"t": [["\u6211", "w\u01d2", "r"], ["\u7684", "de", "u"], ["\u3002", "\u3002", "w"]]}
        ]

    def test_punctuation_two_fields(self):
        result = parse_tokenized_pipe("\uff0c|w")
        assert result[0]["t"][0] == ["\uff0c", "\uff0c", "w"]

    def test_compound_word(self):
        result = parse_tokenized_pipe("\u9ad8\u5174|g\u0101ox\xecng|a")
        assert len(result[0]["t"]) == 1
        token = result[0]["t"][0]
        assert token[0] == "\u9ad8\u5174"
        assert token[1] == "g\u0101ox\xecng"
        assert token[2] == "a"
        assert len(token) == 3

    def test_multiple_sentences(self):
        text = "\u6211|w\u01d2|r\n\u597d|h\u01ceo|a\n\u3002|w\n\n\u4f60|n\u01d0|r\n\u597d|h\u01ceo|a\n\u3002|w"
        result = parse_tokenized_pipe(text)
        assert len(result) == 2
        assert len(result[0]["t"]) == 3
        assert len(result[1]["t"]) == 3

    def test_fence_stripping(self):
        result = parse_tokenized_pipe("```\n\u6211|w\u01d2|r\n\u3002|w\n```")
        assert len(result) == 1

    def test_three_field_token(self):
        result = parse_tokenized_pipe("\u684c\u5b50|zhu\u014dzi|n")
        token = result[0]["t"][0]
        assert len(token) == 3
        assert token[2] == "n"

    def test_emoji_punctuation(self):
        result = parse_tokenized_pipe("\u2026|w\n\uff01|w")
        assert result[0]["t"][0] == ["\u2026", "\u2026", "w"]
        assert result[0]["t"][1] == ["\uff01", "\uff01", "w"]

    def test_expected_chinese_validation_passes(self):
        result = parse_tokenized_pipe(
            "\u6211|w\u01d2|r\n\u597d|h\u01ceo|a",
            expected_chinese=["\u6211\u597d"],
        )
        assert len(result) == 1

    def test_expected_chinese_validation_mismatch_raises(self):
        with pytest.raises(ValueError, match="reconstructed text does not match"):
            parse_tokenized_pipe(
                "\u6211|w\u01d2|r\n\u597d|h\u01ceo|a",
                expected_chinese=["\u4f60\u597d"],  # wrong
            )

    def test_expected_chinese_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="Sentence count mismatch"):
            parse_tokenized_pipe(
                "\u6211|w\u01d2|r\n\u597d|h\u01ceo|a",
                expected_chinese=["\u6211\u597d", "\u4f60\u597d"],
            )


class TestParseTokenizedCompact:
    def test_basic(self):
        result = parse_tokenized_compact("我|wǒ|r||的|de|u||。|w")
        assert result == [
            {"t": [["我", "wǒ", "r"], ["的", "de", "u"], ["。", "。", "w"]]}
        ]

    def test_punctuation_two_fields(self):
        result = parse_tokenized_compact("，|w")
        assert result[0]["t"][0] == ["，", "，", "w"]

    def test_compound_word(self):
        result = parse_tokenized_compact("高兴|gāoxìng|a")
        assert len(result[0]["t"]) == 1
        token = result[0]["t"][0]
        assert token[0] == "高兴"
        assert token[1] == "gāoxìng"
        assert token[2] == "a"
        assert len(token) == 3

    def test_multiple_sentences(self):
        text = "我|wǒ|r||好|hǎo|a||。|w\n你|nǐ|r||好|hǎo|a||。|w"
        result = parse_tokenized_compact(text)
        assert len(result) == 2
        assert len(result[0]["t"]) == 3
        assert len(result[1]["t"]) == 3

    def test_fence_stripping(self):
        result = parse_tokenized_compact("```\n我|wǒ|r||。|w\n```")
        assert len(result) == 1

    def test_three_field_token(self):
        result = parse_tokenized_compact("桌子|zhuōzi|n")
        token = result[0]["t"][0]
        assert len(token) == 3
        assert token[2] == "n"

    def test_english_in_chinese(self):
        result = parse_tokenized_compact('"|w||Jeeves|Jeeves|nr||，|w||"|w')
        assert len(result[0]["t"]) == 4
        assert result[0]["t"][1] == ["Jeeves", "Jeeves", "nr"]

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="No valid token"):
            parse_tokenized_compact("\n\n")

    def test_validation_passes(self):
        result = parse_tokenized_compact(
            "我|wǒ|r||。|w\n你|nǐ|r||好|hǎo|a",
            expected_chinese=["我。", "你好"],
        )
        assert len(result) == 2

    def test_validation_mismatch_raises(self):
        with pytest.raises(ValueError, match="reconstructed text does not match"):
            parse_tokenized_compact(
                "我|wǒ|r||。|w",
                expected_chinese=["我你"],  # wrong
            )

    def test_validation_count_mismatch_raises(self):
        with pytest.raises(ValueError, match="Sentence count mismatch"):
            parse_tokenized_compact(
                "我|wǒ|r||。|w",
                expected_chinese=["我。", "你好"],
            )

    def test_validation_ignores_whitespace(self):
        """Whitespace differences shouldn't cause validation failures."""
        result = parse_tokenized_compact(
            "我|wǒ|r || 。|w",  # extra spaces
            expected_chinese=["我 。"],
        )
        assert len(result) == 1


class TestParseTokenizedFile:
    def test_compact_detected(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("我|wǒ|r||。|w", encoding="utf-8")
        result = parse_tokenized_file(p)
        assert len(result) == 1
        assert result[0]["t"] == [["我", "wǒ", "r"], ["。", "。", "w"]]

    def test_legacy_detected(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("我|wǒ|r\n。|w", encoding="utf-8")
        result = parse_tokenized_file(p)
        assert len(result) == 1
        assert result[0]["t"] == [["我", "wǒ", "r"], ["。", "。", "w"]]

    def test_validation_passed_through(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("我|wǒ|r||。|w", encoding="utf-8")
        result = parse_tokenized_file(p, expected_chinese=["我。"])
        assert len(result) == 1

    def test_legacy_path_validates_expected_chinese(self, tmp_path):
        p = tmp_path / "test.txt"
        p.write_text("我|wǒ|r\n。|w", encoding="utf-8")
        with pytest.raises(ValueError, match="Sentence count mismatch"):
            parse_tokenized_file(p, expected_chinese=["我。", "你好"])

    def test_garbage_without_pipe_separator_is_rejected(self, tmp_path):
        """Non-tokenized LLM output without ``||`` must not bypass validation."""
        p = tmp_path / "test.txt"
        p.write_text(
            "| date | A |\n"
            "| :--- | :--- |\n"
            "| 2022-01-01 | 1 |\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError):
            parse_tokenized_file(p, expected_chinese=["我。"])