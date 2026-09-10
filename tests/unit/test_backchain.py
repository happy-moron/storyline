import pytest
from storyline.podcast.backchain import (
    BackchainResult,
    BackchainStep,
    fix_token_boundaries,
    parse_backchain_output,
    validate_backchain_output,
)


DIALOGUE_LINES = [
    "我想买水果。",
    "我不是中国人。这是我的东西。",
    "我明天要去图书馆看书。",
]

# ── Happy path ──────────────────────────────────────────────────────────

HAPPY_OUTPUT = """水果。
想买水果。
我想买水果。

东西。
我的东西。
这是我的东西。
中国人。这是我的东西。
不是中国人。这是我的东西。
我不是中国人。这是我的东西。

看书。
图书馆看书。
去图书馆看书。
要去图书馆看书。
明天要去图书馆看书。
我明天要去图书馆看书。"""


def test_parse_happy_path():
    results = parse_backchain_output(HAPPY_OUTPUT, DIALOGUE_LINES)
    assert len(results) == 3

    assert results[0].line_index == 0
    assert results[0].original == "我想买水果。"
    assert len(results[0].steps) == 3
    assert results[0].steps[0].text == "水果。"
    assert results[0].steps[0].is_final is False
    assert results[0].steps[1].text == "想买水果。"
    assert results[0].steps[2].text == "我想买水果。"
    assert results[0].steps[2].is_final is True

    assert results[1].line_index == 1
    assert results[1].original == "我不是中国人。这是我的东西。"
    assert len(results[1].steps) == 6

    assert results[2].line_index == 2
    assert results[2].original == "我明天要去图书馆看书。"
    assert len(results[2].steps) == 6


def test_validate_happy_path():
    results = parse_backchain_output(HAPPY_OUTPUT, DIALOGUE_LINES)
    errors = validate_backchain_output(results, DIALOGUE_LINES)
    assert errors == []


# ── Missing sentence ────────────────────────────────────────────────────

MISSING_OUTPUT = """水果。
想买水果。
我想买水果。

东西。
我的东西。
这是我的东西。
不是中国人。这是我的东西。
我不是中国人。这是我的东西。"""


def test_validate_missing_sentence():
    results = parse_backchain_output(MISSING_OUTPUT, DIALOGUE_LINES)
    errors = validate_backchain_output(results, DIALOGUE_LINES)
    assert any("Line 2 not covered" in e for e in errors)


# ── Final mismatch ──────────────────────────────────────────────────────

MISMATCH_OUTPUT = """水果。
想买水果。
我想买水果。WRONG"""


def test_validate_final_mismatch():
    results = parse_backchain_output(MISMATCH_OUTPUT, DIALOGUE_LINES)
    errors = validate_backchain_output(results, DIALOGUE_LINES)
    assert any("mismatch" in e.lower() or "unmatched" in e.lower() for e in errors)


# ── Broken chain ────────────────────────────────────────────────────────

BROKEN_CHAIN_OUTPUT = """水果。
去买水果。
我想买水果。"""


def test_validate_broken_chain():
    results = parse_backchain_output(BROKEN_CHAIN_OUTPUT, DIALOGUE_LINES)
    errors = validate_backchain_output(results, DIALOGUE_LINES)
    assert any("Broken chain" in e or "Final step mismatch" in e for e in errors)


# ── Empty output ────────────────────────────────────────────────────────

def test_parse_empty_output():
    results = parse_backchain_output("", DIALOGUE_LINES)
    assert results == []


def test_validate_empty_output():
    errors = validate_backchain_output([], DIALOGUE_LINES)
    assert len(errors) == len(DIALOGUE_LINES)
    assert all("not covered" in e for e in errors)


# ── Extra blocks (more blocks than lines) ───────────────────────────────

EXTRA_OUTPUT = """水果。
想买水果。
我想买水果。

水果。
想买水果。
我想买水果。"""


def test_validate_extra_blocks():
    results = parse_backchain_output(EXTRA_OUTPUT, DIALOGUE_LINES)
    errors = validate_backchain_output(results, DIALOGUE_LINES)
    assert any("Duplicate" in e or "Unmatched" in e for e in errors)
    assert any("not covered" in e for e in errors)


# ── Single-line input ───────────────────────────────────────────────────

SINGLE_LINE = ["你好。"]


def test_parse_single_line():
    output = "好。\n你好。"
    results = parse_backchain_output(output, SINGLE_LINE)
    assert len(results) == 1
    assert results[0].line_index == 0
    assert results[0].steps[0].text == "好。"
    assert results[0].steps[-1].text == "你好。"


def test_validate_single_line():
    results = parse_backchain_output("好。\n你好。", SINGLE_LINE)
    errors = validate_backchain_output(results, SINGLE_LINE)
    assert errors == []


# ── Whitespace tolerance ────────────────────────────────────────────────

PADDED_OUTPUT = """

好。
你好。
"""


def test_parse_padded_whitespace():
    results = parse_backchain_output(PADDED_OUTPUT, ["你好。"])
    assert len(results) == 1
    assert results[0].steps[-1].text == "你好。"


# ── Duplicate original lines (same text, different index) ───────────────

DUPLICATE_LINES = [
    "你好。",
    "你好。",
    "谢谢。",
]

DUPLICATE_OUTPUT = """好。
你好。

好。
你好。

谢。
谢谢。"""


def test_parse_duplicate_lines():
    results = parse_backchain_output(DUPLICATE_OUTPUT, DUPLICATE_LINES)
    assert len(results) == 3
    indices = [r.line_index for r in results]
    assert indices == [0, 1, 2]


def test_validate_duplicate_lines():
    results = parse_backchain_output(DUPLICATE_OUTPUT, DUPLICATE_LINES)
    errors = validate_backchain_output(results, DUPLICATE_LINES)
    assert errors == []


# ── Single-newline block separation (LLM ignored blank-line instruction) ─

SINGLE_NL_OUTPUT = """吗？
了吗？
预约了吗？
您预约了吗？
欢迎！您预约了吗？
发。
头发。
剪头发。
想剪头发。
我想剪头发。"""

SINGLE_NL_LINES = ["欢迎！您预约了吗？", "我想剪头发。"]


def test_parse_single_newline_separation():
    results = parse_backchain_output(SINGLE_NL_OUTPUT, SINGLE_NL_LINES)
    assert len(results) == 2
    assert results[0].line_index == 0
    assert results[0].original == "欢迎！您预约了吗？"
    assert results[0].steps[-1].text == "欢迎！您预约了吗？"
    assert results[1].line_index == 1
    assert results[1].original == "我想剪头发。"
    assert results[1].steps[-1].text == "我想剪头发。"


def test_validate_single_newline_separation():
    results = parse_backchain_output(SINGLE_NL_OUTPUT, SINGLE_NL_LINES)
    errors = validate_backchain_output(results, SINGLE_NL_LINES)
    assert errors == []


# ── Unicode normalization (halfwidth punctuation) ───────────────────────

HALFWIDTH_OUTPUT = """
生!
先生!

吗?
好吗?
你好吗?
"""

HALFWIDTH_LINES = ["先生！", "你好吗？"]


def test_parse_normalizes_punctuation():
    """LLM uses halfwidth ! ? instead of fullwidth ！ ？"""
    results = parse_backchain_output(HALFWIDTH_OUTPUT, HALFWIDTH_LINES)
    assert len(results) == 2
    assert all(r.line_index != -1 for r in results)


# ── Token boundary fix ──────────────────────────────────────────────────


def _make_results(*step_groups: tuple[str, ...]) -> list[BackchainResult]:
    results: list[BackchainResult] = []
    for i, steps in enumerate(step_groups):
        step_objs = [
            BackchainStep(text=s, is_final=(j == len(steps) - 1))
            for j, s in enumerate(steps)
        ]
        results.append(BackchainResult(
            line_index=i,
            original=steps[-1],
            steps=step_objs,
        ))
    return results


def test_fix_token_boundaries_extends_mid_token():
    """Step starts mid-token → extended to claim the whole word."""
    # "我吃水果" tokenized as: ["我", "吃", "水果"]
    # Bad step "果" starts at char 3, which is within "水果" (chars 2-3)
    # Should be extended to "水果"
    results = _make_results(("果", "水果", "吃水果", "我吃水果"))
    token_texts = [["我", "吃", "水果"]]

    fixed = fix_token_boundaries(results, token_texts)
    assert len(fixed) == 1
    steps = [s.text for s in fixed[0].steps]
    assert steps == ["水果", "吃水果", "我吃水果"]


def test_fix_token_boundaries_no_op_when_already_aligned():
    """Steps already at token boundaries → no changes."""
    results = _make_results(("水果", "吃水果", "我吃水果"))
    token_texts = [["我", "吃", "水果"]]

    fixed = fix_token_boundaries(results, token_texts)
    steps = [s.text for s in fixed[0].steps]
    assert steps == ["水果", "吃水果", "我吃水果"]


def test_fix_token_boundaries_dedup_after_fix():
    """Fixing causes adjacent identical steps → deduplicated."""
    # "XYZW" tokenized as: ["X", "Y", "ZW"]
    # Step "W" is mid-token (in "ZW"), fixes to "ZW"
    # Next step is already "ZW" → dedup merges them
    results = _make_results(("W", "ZW", "YZW", "XYZW"))
    token_texts = [["X", "Y", "ZW"]]

    fixed = fix_token_boundaries(results, token_texts)
    steps = [s.text for s in fixed[0].steps]
    assert steps == ["ZW", "YZW", "XYZW"]


def test_fix_token_boundaries_multi_character_token():
    """Compound word token (图书馆) should not be split."""
    # "我想去图书馆" tokenized as: ["我", "想", "去", "图书馆"]
    # Step "馆" starts at char 5, within "图书馆" (chars 3-5)
    results = _make_results(("馆", "书馆", "图书馆", "去图书馆", "想去图书馆", "我想去图书馆"))
    token_texts = [["我", "想", "去", "图书馆"]]

    fixed = fix_token_boundaries(results, token_texts)
    steps = [s.text for s in fixed[0].steps]
    # "馆"→"图书馆", "书馆"→"图书馆", then dedup → one "图书馆"
    assert steps == ["图书馆", "去图书馆", "想去图书馆", "我想去图书馆"]


def test_fix_token_boundaries_preserves_is_final():
    """The is_final flag should survive the fix and dedup."""
    results = _make_results(("果", "水果", "吃水果", "我吃水果"))
    token_texts = [["我", "吃", "水果"]]

    fixed = fix_token_boundaries(results, token_texts)
    assert fixed[0].steps[-1].is_final is True


def test_fix_token_boundaries_multiple_results():
    """Multiple lines, each with its own tokenization."""
    results = _make_results(
        ("吃", "我吃"),
        ("了", "好了"),
    )
    token_texts = [
        ["我", "吃"],
        ["好", "了"],
    ]

    fixed = fix_token_boundaries(results, token_texts)
    assert len(fixed) == 2
    # First line: both steps already at boundaries (single-char tokens)
    assert [s.text for s in fixed[0].steps] == ["吃", "我吃"]
    assert [s.text for s in fixed[1].steps] == ["了", "好了"]


def test_fix_token_boundaries_oob_index_passthrough():
    """Out-of-bounds line_index → returned unchanged."""
    results = [BackchainResult(
        line_index=99,
        original="你好",
        steps=[BackchainStep(text="好", is_final=False),
               BackchainStep(text="你好", is_final=True)],
    )]
    token_texts = [["你", "好"]]  # only index 0

    fixed = fix_token_boundaries(results, token_texts)
    assert fixed is not results  # new list
    assert fixed[0] is results[0]  # same object (unchanged)


def test_fix_token_boundaries_empty_tokens_passthrough():
    """Empty token list → returned unchanged."""
    results = _make_results(("好", "你好"))
    token_texts = [[]]  # empty tokens for this line

    fixed = fix_token_boundaries(results, token_texts)
    assert [s.text for s in fixed[0].steps] == ["好", "你好"]


def test_fix_token_boundaries_empty_results():
    """Empty results → empty output."""
    fixed = fix_token_boundaries([], [])
    assert fixed == []


def test_fix_token_boundaries_punctuation_in_tokens():
    """Punctuation tokens don't affect the fix."""
    # "你好！" tokenized as: ["你", "好", "！"]
    results = _make_results(("好！", "你好！"))
    token_texts = [["你", "好", "！"]]

    fixed = fix_token_boundaries(results, token_texts)
    steps = [s.text for s in fixed[0].steps]
    assert steps == ["好！", "你好！"]


def test_fix_token_boundaries_dedup_preserves_is_final_flag():
    """When dedup removes a step that was is_final, the survivor gets is_final."""
    # Two identical steps where the second one is is_final
    original = "我吃水果"
    step0 = BackchainStep(text="水果", is_final=False)
    step1 = BackchainStep(text="水果", is_final=True)
    step2 = BackchainStep(text="我吃水果", is_final=True)
    results = [BackchainResult(line_index=0, original=original,
                                steps=[step0, step1, step2])]
    token_texts = [["我", "吃", "水果"]]

    fixed = fix_token_boundaries(results, token_texts)
    steps = fixed[0].steps
    # step0 and step1 dedup; survivor should be is_final since step1 was
    assert len(steps) == 2
    assert steps[0].text == "水果"
    assert steps[0].is_final is True
    assert steps[1].text == "我吃水果"
    assert steps[1].is_final is True