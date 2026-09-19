"""Unit tests for sentence_render.py — config loading, speaker resolution, output writing."""

import tomllib
from pathlib import Path

import pytest

from storyline.audio.sentence_render import (
    _resolve_speaker_map,
    _check_speaker_coverage,
    _sanitise,
)
from storyline.audio.sentence_audio import VoiceSpec, parse_sentence_format, SentenceBlock


# ---------------------------------------------------------------------------
# _resolve_speaker_map
# ---------------------------------------------------------------------------


class TestResolveSpeakerMap:
    def test_builtin_mode(self):
        voices = {
            "1": {"mode": "builtin", "speaker": "Serena"},
            "2": {"mode": "builtin", "speaker": "Ryan"},
        }
        result = _resolve_speaker_map(voices, global_mode="builtin")
        assert result == {
            1: VoiceSpec(mode="builtin", speaker="Serena", ref_audio="", ref_text=""),
            2: VoiceSpec(mode="builtin", speaker="Ryan", ref_audio="", ref_text=""),
        }

    def test_clone_mode(self):
        voices = {
            "1": {"mode": "clone", "ref_audio": "voices/a.wav", "ref_text": "hello"},
        }
        result = _resolve_speaker_map(voices, global_mode="clone")
        assert result == {
            1: VoiceSpec(mode="clone", speaker="", ref_audio="voices/a.wav", ref_text="hello"),
        }

    def test_mode_inherits_global(self):
        voices = {
            "1": {"speaker": "Serena"},
        }
        result = _resolve_speaker_map(voices, global_mode="builtin")
        assert result[1].mode == "builtin"
        assert result[1].speaker == "Serena"

    def test_raises_on_non_numeric_key(self):
        voices = {"teacher": {"mode": "builtin", "speaker": "Serena"}}
        with pytest.raises(ValueError, match="numeric speaker_id"):
            _resolve_speaker_map(voices, global_mode="builtin")

    def test_raises_on_missing_speaker_for_builtin(self):
        voices = {"1": {"mode": "builtin"}}
        with pytest.raises(ValueError, match="speaker"):
            _resolve_speaker_map(voices, global_mode="builtin")

    def test_raises_on_missing_ref_for_clone(self):
        voices = {"1": {"mode": "clone", "ref_audio": "a.wav"}}
        with pytest.raises(ValueError, match="ref_audio.*ref_text"):
            _resolve_speaker_map(voices, global_mode="builtin")

    def test_raises_on_unknown_mode(self):
        voices = {"1": {"mode": "telepathy", "speaker": "Serena"}}
        with pytest.raises(ValueError, match="unknown mode"):
            _resolve_speaker_map(voices, global_mode="builtin")

    def test_empty_voices(self):
        assert _resolve_speaker_map({}, global_mode="builtin") == {}


# ---------------------------------------------------------------------------
# _check_speaker_coverage
# ---------------------------------------------------------------------------


class TestCheckSpeakerCoverage:
    def test_all_covered(self):
        blocks = [
            SentenceBlock("zh", 1, "", "你好", 1),
            SentenceBlock("en", 2, "", "Hello", 2),
        ]
        speaker_map = {
            1: VoiceSpec(mode="builtin", speaker="Serena"),
            2: VoiceSpec(mode="builtin", speaker="Ryan"),
        }
        _check_speaker_coverage(blocks, speaker_map)  # should not raise

    def test_missing_speaker_raises(self):
        blocks = [
            SentenceBlock("zh", 1, "", "你好", 1),
            SentenceBlock("zh", 99, "", "测试", 2),
        ]
        speaker_map = {
            1: VoiceSpec(mode="builtin", speaker="Serena"),
        }
        with pytest.raises(ValueError, match="speaker_id.*99"):
            _check_speaker_coverage(blocks, speaker_map)

    def test_empty_blocks(self):
        _check_speaker_coverage([], {1: VoiceSpec(mode="builtin", speaker="Serena")})


# ---------------------------------------------------------------------------
# _sanitise
# ---------------------------------------------------------------------------


class TestSanitise:
    def test_basic(self):
        assert _sanitise("Hello World") == "Hello_World"

    def test_truncation(self):
        assert len(_sanitise("a" * 50, max_len=10)) == 10

    def test_special_chars(self):
        assert _sanitise("a/b?c!d") == "a_b_c_d"

    def test_empty(self):
        assert _sanitise("") == ""


# ---------------------------------------------------------------------------
# Integration-style: parse + speaker coverage (no HTTP calls)
# ---------------------------------------------------------------------------


class EndToEndConfig:
    """Synthetic end-to-end helper — pure Python, no TTS calls."""

    def test_parse_and_check_roundtrip(self):
        text = """
zh=1
instruct=Warm
你好。

en=2
Hello.
""".strip()
        blocks = parse_sentence_format(text)
        speaker_map = {
            1: VoiceSpec(mode="builtin", speaker="Serena"),
            2: VoiceSpec(mode="builtin", speaker="Ryan"),
        }
        _check_speaker_coverage(blocks, speaker_map)
        assert len(blocks) == 2

    def test_config_toml_is_loadable(self):
        """The default render.toml must be valid TOML and have the expected keys."""
        path = Path(__file__).resolve().parent.parent.parent / "src" / "storyline" / "config" / "render.toml"
        assert path.exists(), f"render.toml not found at {path}"
        with path.open("rb") as f:
            cfg = tomllib.load(f)
        assert "engine" in cfg
        assert "output" in cfg
        assert "voices" in cfg
        assert cfg["engine"]["type"] in ("qwen3", "omnivoice")
        assert cfg["engine"]["mode"] in ("builtin", "clone")