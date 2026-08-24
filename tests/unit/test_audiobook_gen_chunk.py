"""Tests for chunk-based audio generation."""

import json
import math
import pytest

from storyline.audio.audiobook_gen_chunk import (
    compute_line_timestamps,
    _get_chunk_voice,
)


# ---------------------------------------------------------------------------
# _get_chunk_voice
# ---------------------------------------------------------------------------

class TestGetChunkVoice:
    def test_zh_voice(self):
        voice = _get_chunk_voice("zh")
        assert voice["language"] == "chinese"
        assert "mode" in voice
        assert "speaker" in voice

    def test_en_voice(self):
        voice = _get_chunk_voice("en")
        assert voice["language"] == "english"
        assert "mode" in voice
        assert "speaker" in voice

    def test_invalid_language_raises(self):
        with pytest.raises(KeyError):
            _get_chunk_voice("fr")


# ---------------------------------------------------------------------------
# compute_line_timestamps
# ---------------------------------------------------------------------------

def _make_words(texts: list[str], starts: list[float]) -> list[dict]:
    """Build word list from texts and start times, with 0.02s per word."""
    words = []
    for i, t in enumerate(texts):
        s = starts[i]
        words.append({"text": t, "start_time": s, "end_time": s + 0.02})
    return words


class TestComputeLineTimestamps:
    def test_single_line(self):
        words = _make_words(["你", "好"], [0.1, 0.3])
        lines = ["你好。"]
        result = compute_line_timestamps(words, lines, audio_duration=1.0, language="zh")
        assert len(result) == 1
        assert result[0]["start"] == 0.0
        assert result[0]["end"] == 1.0
        assert result[0]["word_count"] == 2

    def test_two_lines_chinese(self):
        words = _make_words(
            ["你", "好", "谢", "谢"],
            [0.1, 0.3, 0.7, 0.9],
        )
        lines = ["你好。", "谢谢。"]
        result = compute_line_timestamps(words, lines, audio_duration=1.5, language="zh")

        assert len(result) == 2

        # Line 1: words 0-1, start=0.0
        assert result[0]["start"] == 0.0
        assert result[0]["word_count"] == 2

        # Boundary = (w1.end + w2.start) / 2 = (0.32 + 0.7) / 2 = 0.51
        assert math.isclose(result[0]["end"], 0.51, abs_tol=0.01)

        # Line 2: words 2-3
        assert math.isclose(result[1]["start"], 0.51, abs_tol=0.01)
        assert result[1]["end"] == 1.5  # audio_duration
        assert result[1]["word_count"] == 2

    def test_two_lines_english(self):
        words = _make_words(
            ["Hello", "world.", "Thank", "you."],
            [0.1, 0.3, 0.7, 0.9],
        )
        lines = ["Hello world.", "Thank you."]
        result = compute_line_timestamps(words, lines, audio_duration=1.5, language="en")

        assert len(result) == 2
        assert result[0]["word_count"] == 2
        assert result[0]["start"] == 0.0
        # Boundary = (w1.end + w2.start) / 2 = (0.32 + 0.7) / 2 = 0.51
        assert math.isclose(result[0]["end"], 0.51, abs_tol=0.01)

        assert result[1]["word_count"] == 2
        assert result[1]["end"] == 1.5

    def test_three_lines(self):
        words = _make_words(
            ["a", "b", "c", "d", "e", "f"],
            [0.1, 0.3, 0.5, 0.7, 0.9, 1.1],
        )
        lines = ["a b", "c d", "e f"]
        result = compute_line_timestamps(words, lines, audio_duration=2.0, language="en")

        assert len(result) == 3

        # Line 1: words 0-1, start=0.0, end = (w1.end + w2.start)/2 = (0.32+0.5)/2 = 0.41
        assert result[0]["start"] == 0.0
        assert math.isclose(result[0]["end"], 0.41, abs_tol=0.01)

        # Line 2: words 2-3, start=0.41, end = (w3.end + w4.start)/2 = (0.72+0.9)/2 = 0.81
        assert math.isclose(result[1]["start"], 0.41, abs_tol=0.01)
        assert math.isclose(result[1]["end"], 0.81, abs_tol=0.01)

        # Line 3: words 4-5, start=0.81, end=2.0
        assert math.isclose(result[2]["start"], 0.81, abs_tol=0.01)
        assert result[2]["end"] == 2.0

    def test_empty_lines(self):
        words = _make_words(["a", "b"], [0.1, 0.3])
        lines = []
        result = compute_line_timestamps(words, lines, audio_duration=1.0, language="en")
        assert result == []

    def test_zero_word_count_line(self):
        words = _make_words(["a", "b"], [0.1, 0.3])
        lines = ["", "a b"]
        result = compute_line_timestamps(words, lines, audio_duration=1.0, language="en")
        assert len(result) == 2
        assert result[0]["word_count"] == 0

    def test_word_count_mismatch(self):
        """Should not crash when alignment has different word count than text."""
        words = _make_words(["a", "b", "c"], [0.1, 0.3, 0.5])
        lines = ["a", "b"]  # 2 words expected, 3 from aligner
        # Should complete without error (clamps word counts)
        result = compute_line_timestamps(words, lines, audio_duration=1.0, language="en")
        assert len(result) == 2
        # Should have used at most available words
        total = sum(r["word_count"] for r in result)
        assert total <= len(words)

    def test_fewer_words_than_expected(self):
        """When aligner returns fewer words, trailing lines get 0 words."""
        words = [
            {"text": "你", "start_time": 0.1, "end_time": 0.3},
            {"text": "好", "start_time": 0.4, "end_time": 0.6},
        ]
        lines = ["你", "好", "吗", "？"]  # 4 lines; ？strips to empty, 吗 mismatched
        result = compute_line_timestamps(words, lines, audio_duration=1.0, language="zh")
        assert len(result) == 4
        assert result[0]["word_count"] == 1
        assert result[1]["word_count"] == 1
        assert result[2]["word_count"] == 0
        assert result[3]["word_count"] == 0

    def test_all_timestamps_rounded(self):
        words = _make_words(["a", "b", "c", "d"], [0.1234, 0.3456, 0.5678, 0.7890])
        lines = ["a b", "c d"]
        result = compute_line_timestamps(words, lines, audio_duration=1.0, language="en")

        for ts in result:
            # Check that values are rounded to 3 decimal places
            assert ts["start"] == round(ts["start"], 3)
            assert ts["end"] == round(ts["end"], 3)


# ---------------------------------------------------------------------------
# Integration: line timestamps from a real-looking alignment
# ---------------------------------------------------------------------------

class TestComputeLineTimestampsRealistic:
    """Tests with realistic word lists resembling forced-aligner output."""

    def test_chinese_conversation(self):
        words = [
            {"text": "你", "start_time": 0.12, "end_time": 0.28},
            {"text": "好", "start_time": 0.30, "end_time": 0.46},
            {"text": "吗", "start_time": 0.48, "end_time": 0.58},
            {"text": "我", "start_time": 0.80, "end_time": 0.96},
            {"text": "很", "start_time": 0.98, "end_time": 1.14},
            {"text": "好", "start_time": 1.16, "end_time": 1.32},
        ]
        lines = ["你好吗？", "我很好。"]
        result = compute_line_timestamps(words, lines, audio_duration=1.5, language="zh")

        # Line 1: words 0-2 (你好吗, no punct in alignment)
        assert result[0]["start"] == 0.0
        # boundary = (0.58 + 0.80) / 2 = 0.69
        assert math.isclose(result[0]["end"], 0.69, abs_tol=0.01)
        assert result[0]["word_count"] == 3

        # Line 2: words 3-5 (我很好, no punct in alignment)
        assert math.isclose(result[1]["start"], 0.69, abs_tol=0.01)
        assert result[1]["end"] == 1.5
        assert result[1]["word_count"] == 3

    def test_english_dialogue(self):
        words = [
            {"text": "Hello", "start_time": 0.10, "end_time": 0.35},
            {"text": "there.", "start_time": 0.38, "end_time": 0.62},
            {"text": "How", "start_time": 0.85, "end_time": 1.05},
            {"text": "are", "start_time": 1.08, "end_time": 1.20},
            {"text": "you?", "start_time": 1.22, "end_time": 1.48},
        ]
        lines = ["Hello there.", "How are you?"]
        result = compute_line_timestamps(words, lines, audio_duration=1.8, language="en")

        assert result[0]["start"] == 0.0
        # boundary = (0.62 + 0.85) / 2 = 0.735
        assert math.isclose(result[0]["end"], 0.735, abs_tol=0.01)
        assert result[0]["word_count"] == 2

        assert math.isclose(result[1]["start"], 0.735, abs_tol=0.01)
        assert result[1]["end"] == 1.8
        assert result[1]["word_count"] == 3