"""Tests for parse_chunk_format — the <line-range>:<instruction> parser."""

import pytest

from storyline.book.parse_chunk_format import (
    parse_chunk_format,
    save_chunks_json,
    load_chunks_json,
)


LINES_6 = [
    "Line one.",
    "Line two.",
    "Line three.",
    "Line four.",
    "Line five.",
    "Line six.",
]

LINES_10 = [
    "Mira stared at the door.",
    "She had heard something.",
    '"Who\'s there?" she whispered.',
    "No answer came.",
    "Just the wind, rattling the old glass.",
    "Then footsteps.",
    "Heavy and slow.",
    "Mira held her breath.",
    "The door creaked open.",
    "A shadow fell across the floor.",
]


# ---------------------------------------------------------------------------
# parse_chunk_format
# ---------------------------------------------------------------------------

class TestParseChunkFormat:
    def test_empty_text_no_instructions(self):
        chunks = parse_chunk_format("", LINES_6)
        assert len(chunks) == 1
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 5]

    def test_no_parsable_lines(self):
        chunks = parse_chunk_format("Just some rambling text.", LINES_6)
        assert len(chunks) == 1
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 5]

    def test_fences_stripped(self):
        text = "```\n3-4: dramatic pause\n```\n"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 3
        assert chunks[1]["instruct"] == "dramatic pause"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [2, 3]

    def test_single_instruction_middle(self):
        text = "3-4: dramatic pause"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 3
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 1]
        assert chunks[1]["instruct"] == "dramatic pause"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [2, 3]
        assert chunks[2]["instruct"] == ""
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [4, 5]

    def test_single_line_instruction(self):
        text = "3: annoyed"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 3
        assert chunks[0]["line_range"] == [0, 1]
        assert chunks[1]["instruct"] == "annoyed"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [2, 2]
        assert chunks[2]["line_range"] == [3, 5]

    def test_instruction_at_start(self):
        text = "1-2: opening lines"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 2
        assert chunks[0]["instruct"] == "opening lines"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 1]
        assert chunks[1]["instruct"] == ""
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [2, 5]

    def test_instruction_at_end(self):
        text = "5-6: closing lines"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 2
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 3]
        assert chunks[1]["instruct"] == "closing lines"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [4, 5]

    def test_multiple_non_overlapping(self):
        text = "2: quiet\n4-5: loud, angry"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 5
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["line_range"] == [0, 0]
        assert chunks[1]["instruct"] == "quiet"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [1, 1]
        assert chunks[2]["instruct"] == ""
        assert chunks[2]["line_range"] == [2, 2]
        assert chunks[3]["instruct"] == "loud, angry"
        assert chunks[3]["instruct_zh"] == ""
        assert chunks[3]["line_range"] == [3, 4]
        assert chunks[4]["instruct"] == ""
        assert chunks[4]["line_range"] == [5, 5]

    def test_multiple_non_overlapping_full_coverage(self):
        """Two adjacent ranges with a gap between — covers all edge cases."""
        text = "1: intro\n3: middle\n5: end"
        chunks = parse_chunk_format(text, ["a", "b", "c", "d", "e"])
        assert len(chunks) == 5
        assert chunks[0] == {"instruct": "intro", "instruct_zh": "", "line_range": [0, 0]}
        assert chunks[1] == {"instruct": "", "instruct_zh": "", "line_range": [1, 1]}
        assert chunks[2] == {"instruct": "middle", "instruct_zh": "", "line_range": [2, 2]}
        assert chunks[3] == {"instruct": "", "instruct_zh": "", "line_range": [3, 3]}
        assert chunks[4] == {"instruct": "end", "instruct_zh": "", "line_range": [4, 4]}

    def test_overlapping_instructions_merged(self):
        text = "2-4: tense\n3-5: loud"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 3
        assert chunks[0] == {"instruct": "", "instruct_zh": "", "line_range": [0, 0]}
        assert chunks[1]["instruct"] == "tense; loud"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [1, 4]
        assert chunks[2] == {"instruct": "", "instruct_zh": "", "line_range": [5, 5]}

    def test_overlap_same_instruct_no_duplicate(self):
        text = "2-4: tense\n3-5: tense"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 3
        assert chunks[1]["instruct"] == "tense"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [1, 4]

    def test_adjacent_same_instruct_merged(self):
        text = "1-2: calm\n3-4: calm"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 2
        assert chunks[0]["instruct"] == "calm"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 3]

    def test_adjacent_different_instruct_not_merged(self):
        text = "1-2: calm\n3-4: tense"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 3
        assert chunks[0]["instruct"] == "calm"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 1]
        assert chunks[1]["instruct"] == "tense"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [2, 3]
        assert chunks[2]["instruct"] == ""
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [4, 5]

    def test_out_of_order_ranges_sorted(self):
        text = "4-5: loud\n1: quiet"
        chunks = parse_chunk_format(text, LINES_6)
        assert chunks[0]["instruct"] == "quiet"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 0]
        assert chunks[1]["instruct"] == ""
        assert chunks[1]["line_range"] == [1, 2]
        assert chunks[2]["instruct"] == "loud"
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [3, 4]
        assert chunks[3]["instruct"] == ""
        assert chunks[3]["line_range"] == [5, 5]

    def test_out_of_bounds_clamped(self):
        text = "0: before start\n7-10: after end"
        chunks = parse_chunk_format(text, LINES_6)
        # "0" clamps to line 1 (index 0); "7-10" is completely beyond 6 lines, dropped
        assert chunks[0]["instruct"] == "before start"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 0]
        assert chunks[1]["instruct"] == ""
        assert chunks[1]["line_range"] == [1, 5]

    def test_line_zero_clamped(self):
        # LLM outputs 0 — becomes index -1, clamped to index 0
        chunks = parse_chunk_format("0: error", LINES_6)
        assert chunks[0]["instruct"] == "error"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 0]
        assert chunks[1]["instruct"] == ""
        assert chunks[1]["line_range"] == [1, 5]

    def test_empty_source_lines(self):
        chunks = parse_chunk_format("1-3: loud", [])
        assert chunks == []

    def test_instruction_with_colon_in_text(self):
        text = "2-3: whisper: very quietly"
        chunks = parse_chunk_format(text, LINES_6)
        assert chunks[1]["instruct"] == "whisper: very quietly"
        assert chunks[1]["instruct_zh"] == ""

    def test_instruction_with_extra_whitespace(self):
        text = "  2-3  :   whisper  "
        chunks = parse_chunk_format(text, LINES_6)
        assert chunks[1]["instruct"] == "whisper"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [1, 2]

    def test_empty_lines_in_input_ignored(self):
        text = "\n\n2: quiet\n\n4-5: loud\n\n"
        chunks = parse_chunk_format(text, LINES_6)
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["line_range"] == [0, 0]
        assert chunks[1]["instruct"] == "quiet"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [1, 1]
        assert chunks[2]["instruct"] == ""
        assert chunks[2]["line_range"] == [2, 2]
        assert chunks[3]["instruct"] == "loud"
        assert chunks[3]["instruct_zh"] == ""
        assert chunks[3]["line_range"] == [3, 4]

    def test_real_world_example(self):
        text = (
            "1-4: tense and frightened, low dramatic tone\n"
            "5: short and clipped; annoyed\n"
            "6-10: relaxed and easy reading pacing, warm friendly tone"
        )
        chunks = parse_chunk_format(text, LINES_10)
        assert len(chunks) == 3
        assert chunks[0]["instruct"] == "tense and frightened, low dramatic tone"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 3]
        assert chunks[1]["instruct"] == "short and clipped; annoyed"
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [4, 4]
        assert chunks[2]["instruct"] == "relaxed and easy reading pacing, warm friendly tone"
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [5, 9]

    def test_single_span_full_text_with_instruct(self):
        text = "1-6: steady narration"
        chunks = parse_chunk_format(text, LINES_6)
        assert len(chunks) == 1
        assert chunks[0]["instruct"] == "steady narration"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 5]

    def test_whitespace_only_input(self):
        chunks = parse_chunk_format("   \n  \n", LINES_6)
        assert len(chunks) == 1
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 5]


# ---------------------------------------------------------------------------
# Dual-language (English + Chinese) format
# ---------------------------------------------------------------------------


class TestDualLanguage:
    def test_paired_english_chinese(self):
        text = (
            "7-11: tense and frightened, low dramatic tone\n"
            "7-11\uff1a紧张而惊恐，戏剧性语气低沉\n"
            "19: short and clipped; annoyed\n"
            "19\uff1a短促而断续；带有恼怒\n"
            "20-27: relaxed and easy reading pacing, warm friendly tone\n"
            "20-27\uff1a阅读节奏轻松从容，语气温暖友好"
        )
        lines = [f"Line {i}." for i in range(1, 31)]
        chunks = parse_chunk_format(text, lines)

        assert len(chunks) == 6

        # Chunk 0: lines 0-5 (gap before 7-11)
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 5]

        # Chunk 1: lines 6-10 (7-11 -> 0-based 6-10)
        assert chunks[1]["instruct"] == "tense and frightened, low dramatic tone"
        assert chunks[1]["instruct_zh"] == "紧张而惊恐，戏剧性语气低沉"
        assert chunks[1]["line_range"] == [6, 10]

        # Chunk 2: lines 11-17 (gap)
        assert chunks[2]["instruct"] == ""
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [11, 17]

        # Chunk 3: line 18 (19 -> 0-based 18)
        assert chunks[3]["instruct"] == "short and clipped; annoyed"
        assert chunks[3]["instruct_zh"] == "短促而断续；带有恼怒"
        assert chunks[3]["line_range"] == [18, 18]

        # Chunk 4: lines 19-26 (20-27 -> 0-based 19-26)
        assert chunks[4]["instruct"] == "relaxed and easy reading pacing, warm friendly tone"
        assert chunks[4]["instruct_zh"] == "阅读节奏轻松从容，语气温暖友好"
        assert chunks[4]["line_range"] == [19, 26]

        # Chunk 5: lines 27-29 (trailing gap after last annotation)
        assert chunks[5]["instruct"] == ""
        assert chunks[5]["instruct_zh"] == ""
        assert chunks[5]["line_range"] == [27, 29]

    def test_english_only_with_colon_still_works(self):
        """Old format (English-only with ASCII colon) still produces valid output."""
        text = "1-3: dramatic pause"
        chunks = parse_chunk_format(text, ["a", "b", "c", "d"])
        assert len(chunks) == 2
        assert chunks[0]["instruct"] == "dramatic pause"
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 2]
        assert chunks[1]["instruct"] == ""
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [3, 3]

    def test_chinese_only_fullwidth_colon(self):
        """Chinese-only lines with fullwidth colon should populate instruct_zh."""
        text = "2-3\uff1a低声快速朗读"
        chunks = parse_chunk_format(text, ["a", "b", "c", "d"])
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 0]
        assert chunks[1]["instruct"] == ""
        assert chunks[1]["instruct_zh"] == "低声快速朗读"
        assert chunks[1]["line_range"] == [1, 2]
        assert chunks[2]["instruct"] == ""
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [3, 3]

    def test_mixed_paired_and_unpaired(self):
        """Some ranges have both languages, some only one."""
        text = (
            "1: calm opening\n"
            "1\uff1a平静开场\n"
            "3-4: tense\n"
            "5\uff1a愤怒"
        )
        chunks = parse_chunk_format(text, ["a", "b", "c", "d", "e", "f"])

        # Chunk 0: line 0 (paired)
        assert chunks[0]["instruct"] == "calm opening"
        assert chunks[0]["instruct_zh"] == "平静开场"
        assert chunks[0]["line_range"] == [0, 0]

        # Chunk 1: line 1 (gap)
        assert chunks[1]["instruct"] == ""
        assert chunks[1]["instruct_zh"] == ""
        assert chunks[1]["line_range"] == [1, 1]

        # Chunk 2: lines 2-3 (en only "tense")
        assert chunks[2]["instruct"] == "tense"
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [2, 3]

        # Chunk 3: line 4 (zh only "愤怒")
        assert chunks[3]["instruct"] == ""
        assert chunks[3]["instruct_zh"] == "愤怒"
        assert chunks[3]["line_range"] == [4, 4]

        # Chunk 4: line 5 (trailing gap)
        assert chunks[4]["instruct"] == ""
        assert chunks[4]["instruct_zh"] == ""
        assert chunks[4]["line_range"] == [5, 5]

    def test_dual_overlap_merges_both_languages(self):
        """Overlapping paired ranges merge both en and zh instructions."""
        text = (
            "2-4: tense\n"
            "2-4\uff1a紧张\n"
            "3-5: loud\n"
            "3-5\uff1a响亮"
        )
        chunks = parse_chunk_format(text, ["a", "b", "c", "d", "e", "f"])
        assert len(chunks) == 3
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["instruct_zh"] == ""
        assert chunks[0]["line_range"] == [0, 0]

        # Merged: 2-4 + 3-5 = 1-4, en: tense; loud, zh: 紧张; 响亮
        assert chunks[1]["instruct"] == "tense; loud"
        assert chunks[1]["instruct_zh"] == "紧张; 响亮"
        assert chunks[1]["line_range"] == [1, 4]

        assert chunks[2]["instruct"] == ""
        assert chunks[2]["instruct_zh"] == ""
        assert chunks[2]["line_range"] == [5, 5]


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------

class TestChunksJsonRoundTrip:
    def test_round_trip(self, tmp_path):
        chunks = [
            {"instruct": "", "instruct_zh": "", "line_range": [0, 1]},
            {"instruct": "Whispered", "instruct_zh": "低语", "line_range": [2, 2]},
            {"instruct": "", "instruct_zh": "", "line_range": [3, 4]},
        ]
        path = tmp_path / "chunks.json"
        save_chunks_json(chunks, path)
        loaded = load_chunks_json(path)
        assert loaded == chunks