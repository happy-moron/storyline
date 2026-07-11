from pathlib import Path

import pytest

from storyline.book.split_text import split_text

INPUT_TEXT = "books_src/childrens/gossie.txt"


class TestSplitText:
    @pytest.fixture
    def output_dir(self, tmp_path):
        return tmp_path / "split_source"

    def test_split_creates_output(self, output_dir):
        assert Path(INPUT_TEXT).exists(), f"Input file not found: {INPUT_TEXT}"

        split_text(INPUT_TEXT, str(output_dir))

        files = sorted(output_dir.glob("*.txt"))
        assert len(files) > 0, "No split files created"

    def test_split_files_have_content(self, output_dir):
        split_text(INPUT_TEXT, str(output_dir))

        for f in output_dir.glob("*.txt"):
            content = f.read_text()
            assert len(content) > 0, f"Empty split file: {f.name}"

    def test_split_output_is_english(self, output_dir):
        """Gossie is an English book, split output should contain English text."""
        split_text(INPUT_TEXT, str(output_dir))

        for f in output_dir.glob("*.txt"):
            content = f.read_text()
            assert any(c.isascii() for c in content[:200]), (
                f"Expected English content in {f.name}, got: {content[:100]}"
            )