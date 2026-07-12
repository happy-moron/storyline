"""RealWorld baseline test for the chunk-based create_book pipeline.

Runs the full pipeline against live infrastructure (LLM + TTS) and
validates every output stage: split → simplify → chunk → translate →
tokenize → audio (chunk-based with forced alignment) → dictionary.

Uses a short source text with a clear tone shift (calm narrative →
excited dialogue) to trigger multi-chunk output.  Keeps total runtime
within ~20 minutes on first execution.

The pipeline only runs when output files are missing, making the suite
re‑runnable without intervention.

Run with:  pytest tests/realworld/ --run-realworld
"""

import json
import shutil
from pathlib import Path

import pytest

from storyline.book.create_book import create_book
from storyline.book.parse_pipe_format import parse_source_file, parse_tokenized_file
from storyline.book.parse_chunk_format import load_chunks_json
from storyline.config.pipeline_config import PipelineConfig
from storyline.services.manager import ServiceManager

# ---------------------------------------------------------------------------
# Paths — everything lives under tests/realworld/output/
# ---------------------------------------------------------------------------
INPUT_TEXT = "tests/realworld/fixtures/mini_test.txt"
AUTHOR = "realworld"
BOOK = "mini_test"

OUTPUT_ROOT = Path("tests/realworld/output")
BOOKS_OUT = OUTPUT_ROOT / "books"
AUDIO_OUT = OUTPUT_ROOT / "audio"
DICT_OUT = OUTPUT_ROOT / "custom_dict.json"

pytestmark = pytest.mark.realworld


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pipeline_already_ran():
    book_dir = BOOKS_OUT / AUTHOR / BOOK
    return (
        (book_dir / "split" / "source").exists()
        and (book_dir / "split" / "simple").exists()
        and (book_dir / "chunks").exists()
        and (book_dir / "pipe" / "source").exists()
        and (book_dir / "pipe" / "tokenized").exists()
    )


def _norm_ws(text: str) -> str:
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="class")
def output_paths():
    BOOKS_OUT.mkdir(parents=True, exist_ok=True)
    AUDIO_OUT.mkdir(parents=True, exist_ok=True)
    DICT_OUT.parent.mkdir(parents=True, exist_ok=True)

    if not DICT_OUT.exists():
        DICT_OUT.write_text("{}", encoding="utf-8")

    book_dir = BOOKS_OUT / AUTHOR / BOOK
    return {
        "book_dir": book_dir,
        "split_dir": book_dir / "split" / "source",
        "simple_dir": book_dir / "split" / "simple",
        "chunks_dir": book_dir / "chunks",
        "source_pipe": book_dir / "pipe" / "source",
        "token_pipe": book_dir / "pipe" / "tokenized",
        "audio": book_dir / "audio",
        "audiobook_dir": AUDIO_OUT / BOOK,
    }


@pytest.fixture(scope="class")
def manager():
    return ServiceManager()


@pytest.fixture(scope="class")
def config(output_paths):
    cfg = PipelineConfig.from_files_and_args(profile_name="test")
    cfg.max_chunks = 1
    cfg.split_chunk_size = 2000
    cfg.skip_simplify = False
    cfg.skip_audio = False
    cfg.audio_profile = "default"
    cfg.books_dir = str(BOOKS_OUT.resolve())
    cfg.audiobook_dir = str(AUDIO_OUT.resolve())
    cfg.dict_file = str(DICT_OUT.resolve())
    return cfg


@pytest.fixture(scope="class")
def run_result(manager, config, output_paths):
    if not _pipeline_already_ran():
        book_dir = output_paths["book_dir"]
        if book_dir.exists():
            shutil.rmtree(book_dir)
        audiobook_dir = output_paths["audiobook_dir"]
        if audiobook_dir.exists():
            shutil.rmtree(audiobook_dir)
        DICT_OUT.write_text("{}", encoding="utf-8")

        create_book(
            INPUT_TEXT,
            AUTHOR,
            config,
            service_manager=manager,
        )
    else:
        if not DICT_OUT.exists():
            DICT_OUT.write_text("{}", encoding="utf-8")

    return output_paths


# ---------------------------------------------------------------------------
# Split stage
# ---------------------------------------------------------------------------

class TestSplit:
    def test_split_files_exist(self, run_result):
        files = sorted(run_result["split_dir"].glob("*.txt"))
        assert len(files) > 0, f"No split files in {run_result['split_dir']}"

    def test_split_files_have_content(self, run_result):
        for f in run_result["split_dir"].glob("*.txt"):
            assert f.stat().st_size > 0, f"Empty split file: {f.name}"

    def test_split_reproduces_source(self, run_result):
        original = Path(INPUT_TEXT).read_text(encoding="utf-8").strip()
        chunks = []
        for f in sorted(run_result["split_dir"].glob("*.txt")):
            chunks.append(f.read_text(encoding="utf-8").strip())
        reconstructed = "\n\n".join(chunks)
        assert _norm_ws(reconstructed) == _norm_ws(original), (
            "Reconstructed split output does not match original source"
        )


# ---------------------------------------------------------------------------
# Simplify stage
# ---------------------------------------------------------------------------

class TestSimplify:
    def test_simple_files_exist(self, run_result):
        files = sorted(run_result["simple_dir"].glob("*.txt"))
        assert len(files) > 0, f"No simplified files in {run_result['simple_dir']}"

    def test_simple_files_have_content(self, run_result):
        for f in run_result["simple_dir"].glob("*.txt"):
            assert f.stat().st_size > 0, f"Empty simplified file: {f.name}"

    def test_simple_one_sentence_per_line(self, run_result):
        for f in sorted(run_result["simple_dir"].glob("*.txt")):
            lines = [l for l in f.read_text(encoding="utf-8").strip().split("\n") if l.strip()]
            assert len(lines) > 0, f"No non-empty lines in {f.name}"

    def test_simple_count_matches_split(self, run_result):
        split_files = sorted(run_result["split_dir"].glob("*.txt"))
        simple_files = sorted(run_result["simple_dir"].glob("*.txt"))
        assert len(simple_files) == len(split_files), (
            f"Simplified file count ({len(simple_files)}) != split file count ({len(split_files)})"
        )


# ---------------------------------------------------------------------------
# Chunk stage (Step 3)
# ---------------------------------------------------------------------------

class TestChunk:
    def test_chunk_json_files_exist(self, run_result):
        files = sorted(run_result["chunks_dir"].glob("*.json"))
        assert len(files) > 0, f"No chunk JSON files in {run_result['chunks_dir']}"

    def test_chunk_json_valid(self, run_result):
        for f in run_result["chunks_dir"].glob("*.json"):
            chunks = load_chunks_json(f)
            assert isinstance(chunks, list), f"Chunks in {f.name} is not a list"
            assert len(chunks) > 0, f"Empty chunk list in {f.name}"

    def test_chunks_have_line_ranges(self, run_result):
        for f in run_result["chunks_dir"].glob("*.json"):
            chunks = load_chunks_json(f)
            for ci, chunk in enumerate(chunks):
                assert "line_range" in chunk, (
                    f"Chunk {ci} in {f.name} missing line_range"
                )
                lr = chunk["line_range"]
                assert len(lr) == 2, f"Chunk {ci} line_range invalid: {lr}"
                assert lr[0] <= lr[1], f"Chunk {ci} inverted range: {lr}"

    def test_chunks_cover_all_lines(self, run_result):
        """Every simplified line must belong to exactly one chunk."""
        for f in run_result["chunks_dir"].glob("*.json"):
            chunks = load_chunks_json(f)
            simple_file = run_result["simple_dir"] / f.name.replace(".json", ".txt")
            assert simple_file.exists(), f"Missing simplified file: {simple_file}"
            simple_lines = [
                l for l in simple_file.read_text(encoding="utf-8").strip().split("\n")
                if l.strip()
            ]

            covered = set()
            for chunk in chunks:
                start, end = chunk["line_range"]
                for i in range(start, end + 1):
                    covered.add(i)

            expected = set(range(len(simple_lines)))
            assert covered == expected, (
                f"Lines not fully covered in {f.name}: "
                f"covered={sorted(covered)}, expected={sorted(expected)}"
            )

    def test_chunks_have_no_gaps(self, run_result):
        """Chunk ranges must be contiguous with no gaps."""
        for f in run_result["chunks_dir"].glob("*.json"):
            chunks = load_chunks_json(f)
            for i in range(1, len(chunks)):
                prev_end = chunks[i - 1]["line_range"][1]
                curr_start = chunks[i]["line_range"][0]
                assert curr_start == prev_end + 1, (
                    f"Gap between chunks {i - 1} and {i} in {f.name}: "
                    f"end={prev_end}, next_start={curr_start}"
                )

    def test_raw_chunk_output_exists(self, run_result):
        files = sorted(run_result["chunks_dir"].glob("*_raw.txt"))
        assert len(files) > 0, f"No raw chunk files in {run_result['chunks_dir']}"


# ---------------------------------------------------------------------------
# Translation (source pipe) stage
# ---------------------------------------------------------------------------

class TestTranslation:
    def test_source_pipe_files_exist(self, run_result):
        files = sorted(run_result["source_pipe"].glob("*.txt"))
        assert len(files) > 0, f"No source pipe files in {run_result['source_pipe']}"

    def test_source_pipe_parseable(self, run_result):
        for f in run_result["source_pipe"].glob("*.txt"):
            sentences = parse_source_file(f)
            assert len(sentences) > 0, f"No sentence pairs parsed from {f.name}"

    def test_source_pipe_has_chinese_and_english(self, run_result):
        for f in run_result["source_pipe"].glob("*.txt"):
            sentences = parse_source_file(f)
            for sent in sentences:
                assert sent["chinese"].strip(), (
                    f"Empty Chinese in {f.name}: {sent}"
                )

    def test_source_pipe_line_count_matches_simplified(self, run_result):
        for f in sorted(run_result["source_pipe"].glob("*.txt")):
            pipe_sentences = parse_source_file(f)
            pipe_line_count = len(pipe_sentences)

            source_name = f.name
            simple_file = run_result["simple_dir"] / source_name
            assert simple_file.exists(), f"Missing simplified file: {simple_file}"
            simple_lines = [
                l for l in simple_file.read_text(encoding="utf-8").strip().split("\n")
                if l.strip()
            ]
            assert pipe_line_count == len(simple_lines), (
                f"Line count mismatch for {source_name}: "
                f"pipe={pipe_line_count}, simplified={len(simple_lines)}"
            )

    def test_source_pipe_english_matches_simplified(self, run_result):
        for f in sorted(run_result["source_pipe"].glob("*.txt")):
            sentences = parse_source_file(f)
            simple_file = run_result["simple_dir"] / f.name
            simple_lines = [
                l for l in simple_file.read_text(encoding="utf-8").strip().split("\n")
                if l.strip()
            ]
            for i, sent in enumerate(sentences):
                english = sent["english"].strip()
                expected = simple_lines[i] if i < len(simple_lines) else ""
                assert english == expected.strip(), (
                    f"English mismatch in {f.name} line {i}: "
                    f"pipe={english!r}, simplified={expected!r}"
                )


# ---------------------------------------------------------------------------
# Tokenization stage
# ---------------------------------------------------------------------------

class TestTokenization:
    def test_token_pipe_files_exist(self, run_result):
        files = sorted(run_result["token_pipe"].glob("*.txt"))
        assert len(files) > 0, f"No token pipe files in {run_result['token_pipe']}"

    def test_token_pipe_parseable(self, run_result):
        for f in run_result["token_pipe"].glob("*.txt"):
            tokenized = parse_tokenized_file(f)
            assert len(tokenized) > 0, f"No sentences parsed from {f.name}"

    def test_token_pipe_tokens_have_three_fields(self, run_result):
        for f in run_result["token_pipe"].glob("*.txt"):
            tokenized = parse_tokenized_file(f)
            for sent in tokenized:
                for token in sent["t"]:
                    assert len(token) == 3, (
                        f"Token in {f.name} missing fields: {token}"
                    )
                    assert all(isinstance(t, str) for t in token), (
                        f"Non-string token field in {f.name}: {token}"
                    )

    def test_token_count_matches_source(self, run_result):
        for f in sorted(run_result["token_pipe"].glob("*.txt")):
            tokenized = parse_tokenized_file(f)
            source_file = run_result["source_pipe"] / f.name
            source_sentences = parse_source_file(source_file)
            assert len(tokenized) == len(source_sentences), (
                f"Sentence count mismatch in {f.name}: "
                f"tokenized={len(tokenized)}, source={len(source_sentences)}"
            )


# ---------------------------------------------------------------------------
# Chunk audio stage (Step 4)
# ---------------------------------------------------------------------------

class TestChunkAudio:
    def test_chunk_audio_files_exist(self, run_result):
        """Chunk audio files exist (zh + en per chunk)."""
        zh_files = sorted(run_result["chunks_dir"].glob("*_zh_*.mp3"))
        en_files = sorted(run_result["chunks_dir"].glob("*_en_*.mp3"))
        assert len(zh_files) > 0, f"No Chinese chunk audio in {run_result['chunks_dir']}"
        assert len(en_files) > 0, f"No English chunk audio in {run_result['chunks_dir']}"
        assert len(zh_files) == len(en_files), (
            f"Mismatched chunk audio: zh={len(zh_files)}, en={len(en_files)}"
        )

    def test_chunk_audio_has_minimum_size(self, run_result):
        for f in run_result["chunks_dir"].glob("*_zh_*.mp3"):
            assert f.stat().st_size > 512, (
                f"Chinese chunk audio too small: {f.name} ({f.stat().st_size} bytes)"
            )
        for f in run_result["chunks_dir"].glob("*_en_*.mp3"):
            assert f.stat().st_size > 512, (
                f"English chunk audio too small: {f.name} ({f.stat().st_size} bytes)"
            )

    def test_chunk_json_enriched_with_audio_metadata(self, run_result):
        """Step 4 enriches chunk JSON with audio paths, timestamps, and words."""
        for f in run_result["chunks_dir"].glob("*.json"):
            chunks = load_chunks_json(f)
            for ci, chunk in enumerate(chunks):
                # Each chunk MUST have been enriched by the audio stage
                assert "audio_zh" in chunk, (
                    f"Chunk {ci} in {f.name} missing audio_zh"
                )
                assert "audio_en" in chunk, (
                    f"Chunk {ci} in {f.name} missing audio_en"
                )
                assert "lines" in chunk, (
                    f"Chunk {ci} in {f.name} missing lines timestamps"
                )
                assert "words_zh" in chunk, (
                    f"Chunk {ci} in {f.name} missing words_zh"
                )
                assert "words_en" in chunk, (
                    f"Chunk {ci} in {f.name} missing words_en"
                )

    def test_line_timestamps_valid(self, run_result):
        """Each line has non-negative start/end with start < end (or == 0)."""
        for f in run_result["chunks_dir"].glob("*.json"):
            chunks = load_chunks_json(f)
            for ci, chunk in enumerate(chunks):
                for li, line_ts in enumerate(chunk.get("lines", [])):
                    assert "start_zh" in line_ts, (
                        f"Chunk {ci} line {li} missing start_zh in {f.name}"
                    )
                    assert "end_zh" in line_ts
                    assert "start_en" in line_ts
                    assert "end_en" in line_ts

                    assert line_ts["start_zh"] >= 0.0, (
                        f"Negative start_zh in chunk {ci} line {li}"
                    )
                    assert line_ts["end_zh"] >= line_ts["start_zh"], (
                        f"end_zh < start_zh in chunk {ci} line {li}"
                    )
                    assert line_ts["start_en"] >= 0.0
                    assert line_ts["end_en"] >= line_ts["start_en"]

    def test_word_timestamps_valid(self, run_result):
        """Word entries have text, start_time, end_time."""
        for f in run_result["chunks_dir"].glob("*.json"):
            chunks = load_chunks_json(f)
            for ci, chunk in enumerate(chunks):
                for lang in ("zh", "en"):
                    words_key = f"words_{lang}"
                    words = chunk.get(words_key, [])
                    assert len(words) > 0, (
                        f"Empty {words_key} in chunk {ci} of {f.name}"
                    )
                    for wi, w in enumerate(words):
                        assert "text" in w, (
                            f"Word {wi} in {words_key} missing text"
                        )
                        assert "start_time" in w
                        assert "end_time" in w
                        assert w["start_time"] >= 0.0
                        assert w["end_time"] >= w["start_time"]

    def test_line_count_matches_source(self, run_result):
        """Number of line entries matches source sentence count."""
        for f in run_result["chunks_dir"].glob("*.json"):
            chunk_id = f.stem
            source_file = run_result["source_pipe"] / f"{chunk_id}.txt"
            assert source_file.exists(), f"Missing source pipe file: {source_file}"
            source_sentences = parse_source_file(source_file)
            expected_total = len(source_sentences)

            chunks = load_chunks_json(f)
            total_lines = sum(
                len(chunk.get("lines", [])) for chunk in chunks
            )
            assert total_lines == expected_total, (
                f"Line timestamp count mismatch in {f.name}: "
                f"lines={total_lines}, source_sentences={expected_total}"
            )

    def test_audiobook_mp3_exists(self, run_result):
        audiobook_files = sorted(run_result["audiobook_dir"].glob("*.mp3"))
        assert len(audiobook_files) > 0, (
            f"No audiobook mp3 in {run_result['audiobook_dir']}"
        )
        for f in audiobook_files:
            assert f.stat().st_size > 1024, (
                f"Audiobook too small: {f.name} ({f.stat().st_size} bytes)"
            )


# ---------------------------------------------------------------------------
# Dictionary stage
# ---------------------------------------------------------------------------

class TestDictionary:
    def test_dict_file_updated(self, run_result, config):
        assert DICT_OUT.exists(), f"Dictionary file missing: {DICT_OUT}"
        content = DICT_OUT.read_text(encoding="utf-8")
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            pytest.fail(f"Dictionary is not valid JSON: {DICT_OUT}")
        assert isinstance(data, dict), (
            f"Dictionary root is not a dict: {type(data)}"
        )