"""Tests for parse_chunk_format — the @instruct/@previous/@next parser."""

import pytest

from storyline.book.parse_chunk_format import (
    parse_chunk_blocks,
    parse_chunk_format,
    save_chunks_json,
    load_chunks_json,
)


# ---------------------------------------------------------------------------
# parse_chunk_blocks
# ---------------------------------------------------------------------------

class TestParseChunkBlocks:
    def test_single_block(self):
        text = (
            "@instruct:\n"
            "@previous:\n"
            "@next: Line one.\n"
        )
        blocks = parse_chunk_blocks(text)
        assert len(blocks) == 1
        assert blocks[0] == {"instruct": "", "previous": "", "next": "Line one."}

    def test_multiple_blocks(self):
        text = (
            "@instruct:\n"
            "@previous:\n"
            "@next: Line one.\n"
            "@instruct: Whispered\n"
            "@previous: Line two.\n"
            "@next: Line three.\n"
            "@instruct:\n"
            "@previous: Line four.\n"
            "@next: Line five.\n"
        )
        blocks = parse_chunk_blocks(text)
        assert len(blocks) == 3
        assert blocks[0]["instruct"] == ""
        assert blocks[0]["previous"] == ""
        assert blocks[0]["next"] == "Line one."
        assert blocks[1]["instruct"] == "Whispered"
        assert blocks[1]["previous"] == "Line two."
        assert blocks[1]["next"] == "Line three."
        assert blocks[2]["instruct"] == ""
        assert blocks[2]["previous"] == "Line four."
        assert blocks[2]["next"] == "Line five."

    def test_multiline_instruct(self):
        text = (
            "@instruct: Gruff, impatient tone.\n"
            "Short, clipped delivery.\n"
            "@previous: Line two.\n"
            "@next: Line three.\n"
        )
        blocks = parse_chunk_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["instruct"] == "Gruff, impatient tone.\nShort, clipped delivery."

    def test_fences_stripped(self):
        text = (
            "```\n"
            "@instruct:\n"
            "@previous:\n"
            "@next: Line one.\n"
            "```\n"
        )
        blocks = parse_chunk_blocks(text)
        assert len(blocks) == 1
        assert blocks[0]["next"] == "Line one."

    def test_empty_input_raises(self):
        with pytest.raises(ValueError, match="No @instruct blocks"):
            parse_chunk_blocks("\n\n")

    def test_no_blocks_raises(self):
        with pytest.raises(ValueError, match="No @instruct blocks"):
            parse_chunk_blocks("Just some text, no blocks here.")

    # --- real examples from the prompt ---

    def test_example_1_dialogue(self):
        text = (
            "@instruct:\n"
            "@previous: \n"
            "@next: Mira stared at the door.\n"
            "\n"
            "@instruct: Frightened whisper\n"
            '@previous: She had heard something. \n'
            '@next: "Who\'s there?" she whispered.\n'
            "\n"
            "@instruct: \n"
            '@previous: "Who\'s there?" she whispered.\n'
            "@next: No answer came.\n"
        )
        blocks = parse_chunk_blocks(text)
        assert len(blocks) == 3

    def test_example_2_long_passage(self):
        text = (
            "@instruct:\n"
            "@previous: \n"
            "@next: The valley opened up beneath them, a patchwork of wheat fields turning gold in the late afternoon light.\n"
        )
        blocks = parse_chunk_blocks(text)
        assert len(blocks) == 1

    def test_example_3_steady_with_beat(self):
        text = (
            "@instruct:\n"
            "@previous:\n"
            "@next: They walked for another hour without speaking. \n"
            "@instruct: Sudden, alarmed, low volume\n"
            "@previous: The path narrowed, then widened again as it crossed an old stone bridge.\n"
            "@next: Halfway across, Tom stopped and grabbed her arm.\n"
            "@instruct:\n"
            '@previous: "Did you hear that?"\n'
            "@next: She hadn't. \n"
        )
        blocks = parse_chunk_blocks(text)
        assert len(blocks) == 3


# ---------------------------------------------------------------------------
# parse_chunk_format
# ---------------------------------------------------------------------------

DIALOGUE_LINES = [
    "Mira stared at the door.",
    "She had heard something.",
    '"Who\'s there?" she whispered.',
    "No answer came.",
    "Just the wind, rattling the old glass.",
]

DESCRIPTIVE_LINES = [
    "The valley opened up beneath them, a patchwork of wheat fields turning gold in the late afternoon light.",
    "Farmhouses dotted the landscape, their chimneys sending up thin columns of smoke.",
    "In the distance, the river caught the sun and threw it back in long, bright ribbons.",
    "It was the kind of view that made you forget, for a moment, why you'd come.",
]

STEADY_LINES = [
    "They walked for another hour without speaking.",
    "The path narrowed, then widened again as it crossed an old stone bridge.",
    "Halfway across, Tom stopped and grabbed her arm.",
    '"Did you hear that?"',
    "She hadn't.",
    "They stood still, listening, until the birdsong resumed and Tom, embarrassed, let go and kept walking.",
]


class TestParseChunkFormat:
    def test_single_chunk_full_text(self):
        text = (
            "@instruct:\n"
            "@previous: \n"
            f"@next: {DESCRIPTIVE_LINES[0]}\n"
        )
        chunks = parse_chunk_format(text, DESCRIPTIVE_LINES)
        assert len(chunks) == 1
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["line_range"] == [0, 3]

    def test_two_chunks(self):
        text = (
            "@instruct:\n"
            "@previous:\n"
            f"@next: {DIALOGUE_LINES[0]}\n"
            "@instruct: Angry tone\n"
            f"@previous: {DIALOGUE_LINES[2]}\n"
            f"@next: {DIALOGUE_LINES[3]}\n"
        )
        chunks = parse_chunk_format(text, DIALOGUE_LINES)
        assert len(chunks) == 2
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["line_range"] == [0, 2]
        assert chunks[1]["instruct"] == "Angry tone"
        assert chunks[1]["line_range"] == [3, 4]

    def test_example_1_dialogue(self):
        text = (
            "@instruct:\n"
            "@previous: \n"
            f"@next: {DIALOGUE_LINES[0]}\n"
            "@instruct: Frightened whisper\n"
            f"@previous: {DIALOGUE_LINES[1]}\n"
            f"@next: {DIALOGUE_LINES[2]}\n"
            "@instruct: \n"
            f"@previous: {DIALOGUE_LINES[2]}\n"
            f"@next: {DIALOGUE_LINES[3]}\n"
        )
        chunks = parse_chunk_format(text, DIALOGUE_LINES)
        assert len(chunks) == 3

        # Chunk 1: lines 0-1, no instruction
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["line_range"] == [0, 1]

        # Chunk 2: line 2 only, "Frightened whisper"
        assert chunks[1]["instruct"] == "Frightened whisper"
        assert chunks[1]["line_range"] == [2, 2]

        # Chunk 3: lines 3-4, no instruction
        assert chunks[2]["instruct"] == ""
        assert chunks[2]["line_range"] == [3, 4]

    def test_example_2_single_chunk(self):
        text = (
            "@instruct:\n"
            "@previous: \n"
            f"@next: {DESCRIPTIVE_LINES[0]}\n"
        )
        chunks = parse_chunk_format(text, DESCRIPTIVE_LINES)
        assert len(chunks) == 1
        assert chunks[0]["instruct"] == ""
        assert chunks[0]["line_range"] == [0, 3]

    def test_example_3_steady_with_beat(self):
        text = (
            "@instruct:\n"
            "@previous:\n"
            f"@next: {STEADY_LINES[0]}\n"
            "@instruct: Sudden, alarmed, low volume\n"
            f"@previous: {STEADY_LINES[1]}\n"
            f"@next: {STEADY_LINES[2]}\n"
            "@instruct:\n"
            f"@previous: {STEADY_LINES[3]}\n"
            f"@next: {STEADY_LINES[4]}\n"
        )
        chunks = parse_chunk_format(text, STEADY_LINES)
        assert len(chunks) == 3

        assert chunks[0]["instruct"] == ""
        assert chunks[0]["line_range"] == [0, 1]

        assert chunks[1]["instruct"] == "Sudden, alarmed, low volume"
        assert chunks[1]["line_range"] == [2, 3]

        assert chunks[2]["instruct"] == ""
        assert chunks[2]["line_range"] == [4, 5]

    # --- validation errors ---

    def test_first_previous_not_empty_raises(self):
        text = (
            "@instruct:\n"
            f"@previous: {DIALOGUE_LINES[0]}\n"
            f"@next: {DIALOGUE_LINES[1]}\n"
        )
        with pytest.raises(ValueError, match="First @previous must be empty"):
            parse_chunk_format(text, DIALOGUE_LINES)

    def test_anchor_not_found_raises(self):
        text = (
            "@instruct:\n"
            "@previous:\n"
            "@next: A line not in the source text.\n"
        )
        with pytest.raises(ValueError, match="not found in source"):
            parse_chunk_format(text, DIALOGUE_LINES)

    def test_not_adjacent_raises(self):
        text = (
            "@instruct:\n"
            "@previous:\n"
            f"@next: {DIALOGUE_LINES[0]}\n"
            "@instruct: Skipped a line\n"
            f"@previous: {DIALOGUE_LINES[0]}\n"
            f"@next: {DIALOGUE_LINES[2]}\n"  # skips line 1
        )
        with pytest.raises(ValueError, match="not adjacent"):
            parse_chunk_format(text, DIALOGUE_LINES)

    def test_single_line_chunk(self):
        lines = ["Line one.", "Line two.", "Line three."]
        text = (
            "@instruct:\n"
            "@previous:\n"
            "@next: Line one.\n"
            "@instruct: Brief\n"
            "@previous: Line one.\n"
            "@next: Line two.\n"
        )
        chunks = parse_chunk_format(text, lines)
        assert len(chunks) == 2
        assert chunks[0]["line_range"] == [0, 0]
        assert chunks[1]["line_range"] == [1, 2]


# ---------------------------------------------------------------------------
# JSON round-trip
# ---------------------------------------------------------------------------

class TestChunksJsonRoundTrip:
    def test_round_trip(self, tmp_path):
        chunks = [
            {"instruct": "", "line_range": [0, 1]},
            {"instruct": "Whispered", "line_range": [2, 2]},
            {"instruct": "", "line_range": [3, 4]},
        ]
        path = tmp_path / "chunks.json"
        save_chunks_json(chunks, path)
        loaded = load_chunks_json(path)
        assert loaded == chunks