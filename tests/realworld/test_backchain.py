"""RealWorld test for back-chain generation and forced alignment.

Runs against live infrastructure (LLM + TTS forced aligner) using an
existing podcast episode.  Validates that:

1. Backchain generation produces valid outputs for all dialogue lines.
2. Forced alignment returns non-empty word boundaries for audio chunks.

Run with:  pytest tests/realworld/ --run-realworld -k test_backchain
"""

import json
from pathlib import Path

import pytest
from pydub import AudioSegment

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.config.pipeline_config import PipelineConfig
from storyline.podcast.audio_gen import add_word_timings
from storyline.podcast.backchain import (
    generate_backchains,
    parse_backchain_output,
    validate_backchain_output,
)
from storyline.podcast.script_parser import parse_script
from storyline.services.manager import ServiceManager

pytestmark = pytest.mark.realworld

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_BOOKS_SRC = _PROJECT_ROOT / "books_src" / "podcasts"
_BOOKS_DIR = _PROJECT_ROOT / "books" / "podcasts"

# Use a single known episode for testing — should be small and have audio
TEST_SLUG = "setting-up-a-new-phone"


# ── Helpers ──────────────────────────────────────────────────────────────

def _find_episode_with_audio():
    """Return (slug, chunk_json_path, script_path) for an episode with audio."""
    for d in sorted(_BOOKS_DIR.iterdir()):
        if not d.is_dir():
            continue
        slug = d.name
        chunk_json = d / "chunks" / f"{slug}_1.json"
        script_txt = _BOOKS_SRC / f"{slug}.txt"
        if not chunk_json.exists() or not script_txt.exists():
            continue
        audio_dir = d / "audio"
        if not audio_dir.exists() or not list(audio_dir.glob("*.mp3")):
            continue
        return slug, chunk_json, script_txt
    return None, None, None


# ── Test: backchain generation with real LLM ────────────────────────────

def test_backchain_generation_real_llm():
    slug, chunk_json_path, script_path = _find_episode_with_audio()
    if not slug:
        pytest.skip("No episode with audio found in books/podcasts/")

    config = PipelineConfig.from_files_and_args()
    service_manager = ServiceManager()
    try:
        if config.llm_provider == "local":
            service_manager.start_if_needed("llm")

        script_text = script_path.read_text(encoding="utf-8")
        script = parse_script(script_text)
        dialogue_lines = [line.chinese for line in script.dialogue]
        dialogue_lines = [l for l in dialogue_lines if l.strip()]

        assert len(dialogue_lines) > 0, "Episode has no dialogue lines"

        results = generate_backchains(
            dialogue_lines, slug, _BOOKS_DIR / slug,
            resolve_prompt=config.resolve_prompt,
            models=config.models,
            llm_timeout_s=config.llm_timeout_s,
            llm_retries=config.llm_retries,
            extra_options=None,
        )

        assert len(results) == len(dialogue_lines), (
            f"Expected {len(dialogue_lines)} results, got {len(results)}"
        )

        errors = validate_backchain_output(results, dialogue_lines)
        assert errors == [], (
            f"Validation errors: {'; '.join(errors[:5])}"
        )

        for r in results:
            assert r.original == dialogue_lines[r.line_index], (
                f"Line {r.line_index}: '{r.original[:30]}' != '{dialogue_lines[r.line_index][:30]}'"
            )
            for i in range(len(r.steps) - 1):
                assert r.steps[i + 1].text.endswith(r.steps[i].text), (
                    f"Broken chain line {r.line_index} step {i}: "
                    f"'{r.steps[i+1].text[:30]}' does not end with '{r.steps[i].text[:30]}'"
                )

    finally:
        if config.llm_provider == "local":
            service_manager.stop("llm")


# ── Test: forced alignment with real TTS service ────────────────────────

def test_forced_alignment_real_tts():
    slug, chunk_json_path, script_path = _find_episode_with_audio()
    if not slug:
        pytest.skip("No episode with audio found in books/podcasts/")

    config = PipelineConfig.from_files_and_args()
    service_manager = ServiceManager()
    try:
        service_manager.start_if_needed("tts")
        service = Qwen3TTSService()

        script_text = script_path.read_text(encoding="utf-8")
        script = parse_script(script_text)

        # Only align the first chunk to keep the test fast
        with open(chunk_json_path, encoding="utf-8") as f:
            chunk_data = json.load(f)

        chunks = chunk_data["chunks"]
        audio_dir = _BOOKS_DIR / slug / "audio"

        # Restore chunk_data to clean state (no words field)
        for chunk in chunks:
            if chunk.get("lines") and chunk["lines"][0].get("words"):
                del chunk["lines"][0]["words"]

        # Align just the first chunk that has audio
        aligned = 0
        for chunk in chunks:
            audio_file = chunk.get("audio_zh")
            if not audio_file:
                continue
            audio_path = audio_dir / audio_file
            if not audio_path.exists():
                continue

            line_idx = chunk["line_range"][0]
            if line_idx >= len(script.dialogue):
                continue

            chinese_text = script.dialogue[line_idx].chinese
            if not chinese_text.strip():
                continue

            audio = AudioSegment.from_file(audio_path)
            words = service.forced_align(audio, chinese_text, "chinese")

            assert isinstance(words, list), f"Expected list, got {type(words)}"
            assert len(words) > 0, f"No word boundaries returned for '{chinese_text[:30]}'"

            for w in words:
                assert "text" in w, f"Missing 'text' in word: {w}"
                assert "start_time" in w, f"Missing 'start_time' in word: {w}"
                assert "end_time" in w, f"Missing 'end_time' in word: {w}"
                assert isinstance(w["start_time"], (int, float))
                assert isinstance(w["end_time"], (int, float))
                assert w["start_time"] < w["end_time"], (
                    f"start_time {w['start_time']} >= end_time {w['end_time']} "
                    f"for word '{w['text']}'"
                )

            aligned = 1
            break

        assert aligned == 1, "Failed to align any chunks"

    finally:
        service_manager.stop("tts")
        service_manager.stop("llm")