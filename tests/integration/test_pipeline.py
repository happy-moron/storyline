"""Integration tests for the full create_book pipeline (requires live services).

Run with:  pytest tests/integration/test_pipeline.py --run-integration --run-slow
"""

import os
import time
from pathlib import Path

import pytest

from storyline.audio.audiobook_gen_qwen3 import process_json_to_audio
from storyline.book.create_book import create_book
from storyline.config.pipeline_config import PipelineConfig
from storyline.services.manager import ServiceManager

INPUT_TEXT = "books_src/childrens/gossie.txt"
AUTHOR = "childrens"
BOOK = "gossie"
BASE_DIR = f"books/{AUTHOR}/{BOOK}"


@pytest.mark.integration
@pytest.mark.slow
class TestFullPipeline:
    @pytest.fixture(scope="class")
    def manager(self):
        return ServiceManager()

    @pytest.fixture(scope="class")
    def run_pipeline(self, manager):
        """Run the full pipeline once and return paths for verification."""
        config = PipelineConfig.from_files_and_args(profile_name="test")
        # Override specific values for this test suite
        config.max_chunks = 1
        config.skip_simplify = True
        config.skip_audio = False
        config.audio_profile = "default"

        create_book(
            INPUT_TEXT,
            AUTHOR,
            config,
            service_manager=manager,
        )

        return {
            "base": Path(BASE_DIR),
            "source_pipe": Path(BASE_DIR) / "pipe" / "source",
            "token_pipe": Path(BASE_DIR) / "pipe" / "tokenized",
            "audio": Path(BASE_DIR) / "audio",
            "audiobook_dir": config.audiobook_output_dir(BOOK),
        }

    # -- Split output --

    def test_split_files_exist(self, run_pipeline):
        split_dir = run_pipeline["base"] / "split" / "source"
        files = sorted(split_dir.glob("*.txt"))
        assert len(files) > 0, f"No split files in {split_dir}"

    def test_split_files_have_content(self, run_pipeline):
        split_dir = run_pipeline["base"] / "split" / "source"
        for f in split_dir.glob("*.txt"):
            assert f.stat().st_size > 0, f"Empty split file: {f.name}"

    # -- Pipe source (translation) --

    def test_source_pipe_files_exist(self, run_pipeline):
        files = sorted(run_pipeline["source_pipe"].glob("*.txt"))
        assert len(files) > 0, f"No source pipe files in {run_pipeline['source_pipe']}"

    def test_source_pipe_contains_pipe_triple_delimiters(self, run_pipeline):
        for f in run_pipeline["source_pipe"].glob("*.txt"):
            content = f.read_text(encoding="utf-8")
            assert "|||" in content, f"{f.name} missing ||| delimiter"

    # -- Pipe tokenized (POS annotation) --

    def test_token_pipe_files_exist(self, run_pipeline):
        files = sorted(run_pipeline["token_pipe"].glob("*.txt"))
        assert len(files) > 0, f"No token pipe files in {run_pipeline['token_pipe']}"

    def test_token_pipe_contains_pipe_delimiters(self, run_pipeline):
        for f in run_pipeline["token_pipe"].glob("*.txt"):
            content = f.read_text(encoding="utf-8")
            assert "|" in content, f"{f.name} missing token delimiters"

    # -- Audio --

    def test_audio_files_exist(self, run_pipeline):
        files = sorted(run_pipeline["audio"].glob("*.mp3"))
        assert len(files) > 0, f"No audio files in {run_pipeline['audio']}"

    def test_audio_files_have_minimum_size(self, run_pipeline):
        for f in run_pipeline["audio"].glob("*.mp3"):
            assert f.stat().st_size > 512, f"Audio file too small: {f.name} ({f.stat().st_size} bytes)"

    def test_audiobook_mp3_exists(self, run_pipeline):
        audiobook = Path(run_pipeline["audiobook_dir"]) / f"{BOOK}_1.mp3"
        assert audiobook.exists(), f"Audiobook missing: {audiobook}"
        assert audiobook.stat().st_size > 1024, f"Audiobook too small: {audiobook.stat().st_size} bytes"


@pytest.mark.slow
@pytest.mark.integration
class TestAudioGenFromPipe:
    """Test audio generation from an existing pipe source file."""

    @pytest.fixture(scope="class")
    def manager(self):
        return ServiceManager()

    @pytest.fixture(scope="class")
    def source_file(self):
        """Use the first pipe source file from the pipeline output."""
        pipe_dir = Path(BASE_DIR) / "pipe" / "source"
        files = sorted(pipe_dir.glob("*.txt"))
        if not files:
            pytest.skip(f"No source pipe files in {pipe_dir}")
        return files[0]

    def test_generate_audiobook(self, manager, source_file):
        output = Path("/tmp") / f"test_gossie_audio_{int(time.time())}.mp3"
        try:
            # Ensure TTS is running, stop LLM if needed
            from storyline.services.manager import ServiceStatus
            if manager.get_status("tts") == ServiceStatus.OFFLINE:
                if manager.get_status("llm") == ServiceStatus.ONLINE:
                    manager.stop("llm")
                manager.start("tts")

            process_json_to_audio(
                str(source_file),
                str(output),
                profile_key="default",
                standalone_file=None,
            )

            assert output.exists(), f"Audio file not created: {output}"
            assert output.stat().st_size > 1024, (
                f"Audio too small: {output.stat().st_size} bytes"
            )
        finally:
            if output.exists():
                output.unlink()