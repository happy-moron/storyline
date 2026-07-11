"""Tests for storyline.benchmark.translate_compare."""

import pytest
from unittest.mock import patch, MagicMock

from storyline.benchmark import TaskResult, TranslateValidation, TokenizeValidation
from storyline.benchmark.translate_compare import (
    aggregate_translations,
    call_translation_check,
    parse_check_response,
    compute_error_ratio,
    partition_errors_by_block,
    _ERROR_LINE_RE,
)


# ---------------------------------------------------------------------------
# Error line regex
# ---------------------------------------------------------------------------

class TestErrorLinePattern:
    def test_matches_grammar_error(self):
        m = _ERROR_LINE_RE.match("5:grammar:wrong particle usage")
        assert m is not None
        assert m.group(1) == "5"
        assert m.group(2) == "grammar"
        assert m.group(3) == "wrong particle usage"

    def test_matches_style_error(self):
        m = _ERROR_LINE_RE.match("12:style:unidiomatic phrasing")
        assert m is not None
        assert m.group(2) == "style"

    def test_rejects_bad_type(self):
        assert _ERROR_LINE_RE.match("1:spelling:bad word") is None
        assert _ERROR_LINE_RE.match("1:grammar") is None
        assert _ERROR_LINE_RE.match("abc:grammar:desc") is None
        assert _ERROR_LINE_RE.match("just some text") is None

    def test_matches_multi_digit(self):
        m = _ERROR_LINE_RE.match("150:style:long sentence issue")
        assert m is not None
        assert m.group(1) == "150"

    def test_matches_colon_in_description(self):
        m = _ERROR_LINE_RE.match("3:style:phrase: awkward: really bad")
        assert m is not None
        assert m.group(3) == "phrase: awkward: really bad"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_translate_result(
    eval_case_id: str = "block_01",
    attempt: int = 1,
    *,
    response_raw: str = "你好\n世界\n",
    valid: bool = True,
) -> TaskResult:
    lines = [l.strip() for l in response_raw.strip().split("\n") if l.strip()]
    return TaskResult(
        task="translate",
        model="test-model",
        eval_case_id=eval_case_id,
        attempt=attempt,
        wall_time_ms=100.0,
        response_chars=len(response_raw),
        valid=valid,
        _response_raw=response_raw,
        validation=TranslateValidation(
            parseable=bool(lines),
            sentence_count_match=True,
            all_have_chinese=True,
            no_refusals=True,
            total_sentences=len(lines),
        ),
    )


# ---------------------------------------------------------------------------
# aggregate_translations
# ---------------------------------------------------------------------------

class TestAggregateTranslations:
    def test_single_result(self):
        tr = _make_translate_result(response_raw="你好\n世界\n")
        text, ranges = aggregate_translations([tr])
        assert text == "你好\n世界"
        assert ranges == [(1, 2)]

    def test_multiple_results(self):
        r1 = _make_translate_result("block_01", response_raw="A\nB\n")
        r2 = _make_translate_result("block_02", response_raw="C\nD\nE\n")
        text, ranges = aggregate_translations([r1, r2])
        assert text == "A\nB\nC\nD\nE"
        assert ranges == [(1, 2), (3, 5)]

    def test_skips_invalid_results(self):
        r1 = _make_translate_result("block_01", response_raw="A\nB\n", valid=False)
        r2 = _make_translate_result("block_02", response_raw="C\nD\n")
        text, ranges = aggregate_translations([r1, r2])
        assert text == "C\nD"
        assert ranges == [(0, 0), (1, 2)]

    def test_skips_empty_response(self):
        r1 = _make_translate_result("block_01", response_raw="A\n")
        r2 = _make_translate_result("block_02", response_raw="")
        text, ranges = aggregate_translations([r1, r2])
        assert text == "A"
        assert ranges == [(1, 1), (0, 0)]

    def test_empty_input(self):
        text, ranges = aggregate_translations([])
        assert text == ""
        assert ranges == []

    def test_mixed_tasks(self):
        translate = _make_translate_result("block_01", response_raw="A\nB\n")
        tokenize = TaskResult(
            task="tokenize", model="m", eval_case_id="block_01",
            attempt=1, wall_time_ms=100.0, response_chars=10,
            validation=TokenizeValidation(True, True, True, errors=None),
        )
        text, ranges = aggregate_translations([translate, tokenize])
        assert text == "A\nB"
        assert ranges == [(1, 2), (0, 0)]

    def test_three_blocks_correct_ranges(self):
        r1 = _make_translate_result("b1", response_raw="S1\nS2\n")
        r2 = _make_translate_result("b2", response_raw="S3\nS4\nS5\n")
        r3 = _make_translate_result("b3", response_raw="S6\n")
        text, ranges = aggregate_translations([r1, r2, r3])
        assert text == "S1\nS2\nS3\nS4\nS5\nS6"
        assert ranges == [(1, 2), (3, 5), (6, 6)]


# ---------------------------------------------------------------------------
# call_translation_check
# ---------------------------------------------------------------------------

class TestCallTranslationCheck:
    def test_calls_prompt_runner(self):
        with patch("storyline.benchmark.translate_compare.PromptRunner") as MockPR:
            mock_runner = MockPR.return_value
            mock_runner.run.return_value = "1:grammar:bad\n2:style:awkward\n"

            result = call_translation_check("你好\n世界\n", ["remote/model"])

            MockPR.assert_called_once()
            mock_runner.run.assert_called_once()
            args, kwargs = mock_runner.run.call_args
            assert args[0] == "prompts/check_translation_blind.txt"
            assert "你好" in args[1]
            assert kwargs["models"] == ["remote/model"]
            assert "1:grammar:bad" in result

    def test_strips_think_tags(self):
        with patch("storyline.benchmark.translate_compare.PromptRunner") as MockPR:
            mock_runner = MockPR.return_value
            mock_runner.run.return_value = "   1:grammar:bad\n"

            result = call_translation_check("text", ["m"])
            assert " thinking" not in result
            assert "1:grammar:bad" in result

    def test_strips_markdown_fences(self):
        with patch("storyline.benchmark.translate_compare.PromptRunner") as MockPR:
            mock_runner = MockPR.return_value
            mock_runner.run.return_value = "```\n1:grammar:bad\n```"

            result = call_translation_check("text", ["m"])
            assert "```" not in result

    def test_raises_on_none_response(self):
        with patch("storyline.benchmark.translate_compare.PromptRunner") as MockPR:
            mock_runner = MockPR.return_value
            mock_runner.run.return_value = None

            with pytest.raises(RuntimeError, match="returned None"):
                call_translation_check("text", ["m"])


# ---------------------------------------------------------------------------
# parse_check_response
# ---------------------------------------------------------------------------

class TestParseCheckResponse:
    def test_parses_valid_errors(self):
        response = "1:grammar:incorrect measure word\n2:style:awkward phrasing\n"
        errors = parse_check_response(response)
        assert errors == [
            "1:grammar:incorrect measure word",
            "2:style:awkward phrasing",
        ]

    def test_filters_non_error_lines(self):
        response = (
            "Here are the errors:\n"
            "1:grammar:bad grammar\n"
            "3:style:bad style\n"
            "No more errors.\n"
        )
        errors = parse_check_response(response)
        assert errors == [
            "1:grammar:bad grammar",
            "3:style:bad style",
        ]

    def test_empty_response(self):
        assert parse_check_response("") == []
        assert parse_check_response("\n\n") == []

    def test_handles_extra_whitespace(self):
        response = "  4:grammar:incorrect word order  \n  7:style:unclear reference  \n"
        errors = parse_check_response(response)
        assert errors == [
            "4:grammar:incorrect word order",
            "7:style:unclear reference",
        ]

    def test_full_example_response(self):
        response = (
            "2:style: Unidiomatic phrasing: stood up using his hand\n"
            "7:style: Unclear reference: her former wheel\n"
            "9:style: Awkward phrasing: in her head around\n"
            "30:grammar: Incorrect grammar/collocation: find out of the house\n"
        )
        errors = parse_check_response(response)
        assert len(errors) == 4
        assert errors[0] == "2:style: Unidiomatic phrasing: stood up using his hand"
        assert errors[3] == "30:grammar: Incorrect grammar/collocation: find out of the house"


# ---------------------------------------------------------------------------
# compute_error_ratio
# ---------------------------------------------------------------------------

class TestComputeErrorRatio:
    def test_basic_ratio(self):
        assert compute_error_ratio(["e1", "e2"], 10) == 0.2

    def test_no_errors(self):
        assert compute_error_ratio([], 10) == 0.0

    def test_zero_sentences(self):
        assert compute_error_ratio(["e1"], 0) == 0.0

    def test_more_errors_than_sentences(self):
        assert compute_error_ratio(["e1", "e2", "e3"], 2) == 1.5


# ---------------------------------------------------------------------------
# partition_errors_by_block
# ---------------------------------------------------------------------------

class TestPartitionErrorsByBlock:
    def test_partitions_correctly(self):
        errors = [
            "1:grammar:error in block 1",
            "2:style:also block 1",
            "3:grammar:error in block 2",
            "5:style:also block 2",
        ]
        block_ranges = [(1, 2), (3, 6)]
        result = partition_errors_by_block(errors, block_ranges)
        assert len(result) == 2
        assert result[0] == ["1:grammar:error in block 1", "2:style:also block 1"]
        assert result[1] == ["3:grammar:error in block 2", "5:style:also block 2"]

    def test_no_errors(self):
        result = partition_errors_by_block([], [(1, 2), (3, 5)])
        assert result == [[], []]

    def test_error_falls_outside_ranges(self):
        errors = ["99:style:orphaned"]
        block_ranges = [(1, 5)]
        result = partition_errors_by_block(errors, block_ranges)
        assert result == [[]]

    def test_empty_block_ranges(self):
        result = partition_errors_by_block(["1:grammar:bad"], [])
        assert result == []

    def test_all_errors_first_block(self):
        errors = ["1:grammar:a", "2:style:b"]
        block_ranges = [(1, 3), (4, 6)]
        result = partition_errors_by_block(errors, block_ranges)
        assert result == [["1:grammar:a", "2:style:b"], []]

    def test_errors_on_boundary(self):
        errors = ["2:style:boundary"]
        block_ranges = [(1, 2), (3, 5)]
        result = partition_errors_by_block(errors, block_ranges)
        assert result == [["2:style:boundary"], []]

    def test_drops_invalid_error_lines(self):
        errors = ["not an error", "1:grammar:valid", "also not valid"]
        block_ranges = [(1, 3)]
        result = partition_errors_by_block(errors, block_ranges)
        assert result == [["1:grammar:valid"]]


# ---------------------------------------------------------------------------
# Integration: full flow
# ---------------------------------------------------------------------------

class TestIntegrationFlow:
    def test_full_aggregate_check_parse_flow(self):
        r1 = _make_translate_result("block_01", response_raw="你好\n世界\n")
        r2 = _make_translate_result("block_02", response_raw="你好吗\n我很好\n")

        text, ranges = aggregate_translations([r1, r2])
        assert text == "你好\n世界\n你好吗\n我很好"
        assert ranges == [(1, 2), (3, 4)]

        with patch("storyline.benchmark.translate_compare.PromptRunner") as MockPR:
            mock_runner = MockPR.return_value
            mock_runner.run.return_value = "1:grammar:bad\n3:style:awkward\n"

            response = call_translation_check(text, ["remote/model"])
            errors = parse_check_response(response)

        assert errors == ["1:grammar:bad", "3:style:awkward"]
        ratio = compute_error_ratio(errors, 4)
        assert ratio == 0.5

        per_block = partition_errors_by_block(errors, ranges)
        assert per_block[0] == ["1:grammar:bad"]
        assert per_block[1] == ["3:style:awkward"]