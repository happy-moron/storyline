"""Unit tests for storyline.book.breakdown."""
import pytest
from storyline.book.breakdown import (
    parse_breakdown_output,
    validate_breakdown_output,
    _is_likely_header,
)


# ---------------------------------------------------------------------------
# _is_likely_header (replaces _find_matching_line)
# ---------------------------------------------------------------------------

def test_likely_header_exact_match():
    sources = ["虽然我今天工作很累，但是还是要回家陪你们吃饭。", "哎呀，我不想吃家里的菜"]
    assert _is_likely_header("虽然我今天工作很累，但是还是要回家陪你们吃饭。", sources) == 0
    assert _is_likely_header("哎呀，我不想吃家里的菜", sources) == 1


def test_likely_header_no_match():
    sources = ["虽然", "哎呀"]
    assert _is_likely_header("不存在的", sources) is None


def test_likely_header_cjk_relaxed():
    """Extra punctuation should still match via CJK-only fallback."""
    sources = ["虽然今天工作很累", "哎呀不想吃"]
    assert _is_likely_header("虽然今天工作很累。", sources) == 0


# ---------------------------------------------------------------------------
# parse_breakdown_output
# ---------------------------------------------------------------------------

def test_parse_basic():
    """Standard LLM output: each Chinese line + blank + explanations + blank."""
    raw = """虽然我今天工作很累，但是还是要回家陪你们吃饭。

虽然 (suīrán) means "although"
工作 (gōngzuò) means "work"
很累 (hěn lèi) means "very tired"

哎呀，我不想吃家里的菜

哎呀 (āiyā) is an exclamation
不想 (bù xiǎng) means "don't want"
"""
    sources = ["虽然我今天工作很累，但是还是要回家陪你们吃饭。", "哎呀，我不想吃家里的菜"]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    assert len(result[0]) == 3, f"Expected 3 points, got {len(result[0])}: {result[0]}"
    assert len(result[1]) == 2
    assert "suīrán" in result[0][0]
    assert "āiyā" in result[1][0]


def test_parse_out_of_order():
    """Should handle lines appearing in different order than source."""
    raw = """line B

point B1

line A

point A1
point A2
"""
    sources = ["line A", "line B"]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    assert len(result[0]) == 2  # line A: "point A1", "point A2"
    assert len(result[1]) == 1  # line B: "point B1"


def test_parse_missing_line():
    """Output missing a line should result in empty breakdown for that line."""
    raw = """line A

point A1
"""
    sources = ["line A", "line B"]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    assert len(result[0]) == 1  # line A has one point
    assert len(result[1]) == 0  # line B is missing


def test_parse_multiline_points():
    """Multiple explanation lines per block."""
    raw = """line one

long explanation line 1
line 2 of explanation

line two

point two
"""
    sources = ["line one", "line two"]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    assert len(result[0]) == 2  # two lines of explanation
    assert len(result[1]) == 1


def test_parse_with_fences():
    raw = "```\nline one\n\npoint 1\n\nline two\n\npoint 2\n```"
    sources = ["line one", "line two"]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    assert len(result[0]) == 1
    assert len(result[1]) == 1


def test_parse_empty_output():
    with pytest.raises(ValueError, match="is empty"):
        parse_breakdown_output("", ["line A"])


def test_parse_header_with_extra_lines():
    """Edge case where header block has extra lines beyond the Chinese text."""
    raw = """line A
extra-in-header

point A1

line B

point B1
"""
    sources = ["line A", "line B"]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    # The "extra-in-header" line becomes part of A's explanations
    assert len(result[0]) == 2  # "extra-in-header" + "point A1"
    assert len(result[1]) == 1


def test_parse_single_block_fallback():
    """When LLM doesn't use blank lines, fall back to in-block parsing."""
    raw = "line A\npoint A1\npoint A2\nline B\npoint B1"
    sources = ["line A", "line B"]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    assert len(result[0]) == 2
    assert len(result[1]) == 1


def test_parse_sample_from_prompt():
    """Parse the actual sample format from the prompt."""
    raw = """虽然我今天工作很累，但是我还是要回家陪你们吃饭。

虽然 (suīrán) means "although." A concession, like saying "even though" something is true.
工作 (gōngzuò) — Can be a noun meaning "work" or "job," but used here as a verb, "to work."
很累 (hěn lèi) — "very tired" or "exhausted."


哎呀，我不想吃家里的菜，我只想吃肉！

哎呀 (āiyā) — An exclamation expressing frustration, annoyance, or mild complaint.
不想 (bù xiǎng) — "don't want to."
"""
    sources = [
        "虽然我今天工作很累，但是我还是要回家陪你们吃饭。",
        "哎呀，我不想吃家里的菜，我只想吃肉！",
    ]
    result = parse_breakdown_output(raw, sources)
    assert len(result) == 2
    assert len(result[0]) >= 3
    assert len(result[1]) >= 2


# ---------------------------------------------------------------------------
# validate_breakdown_output
# ---------------------------------------------------------------------------

def test_validate_clean():
    breakdowns = [
        ["point 1", "point 2"],
        ["point 3"],
    ]
    sources = ["line A", "line B"]
    errors = validate_breakdown_output(breakdowns, sources)
    assert errors == []


def test_validate_missing_line():
    breakdowns = [
        ["point 1"],
        [],
    ]
    sources = ["line A", "line B"]
    errors = validate_breakdown_output(breakdowns, sources)
    assert len(errors) == 1
    assert "Line 1" in errors[0]


def test_validate_point_identical_to_source():
    breakdowns = [
        ["line A"],  # identical to source
        ["point 2"],
    ]
    sources = ["line A", "line B"]
    errors = validate_breakdown_output(breakdowns, sources)
    assert len(errors) == 1
    assert "identical to source" in errors[0]


def test_validate_mixed_errors():
    breakdowns = [
        [],
        ["line B"],  # identical to source
    ]
    sources = ["line A", "line B"]
    errors = validate_breakdown_output(breakdowns, sources)
    assert len(errors) == 2