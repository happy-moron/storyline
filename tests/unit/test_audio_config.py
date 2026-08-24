import tomllib
from pathlib import Path

import pytest

from storyline.audio.audiobook_gen_base import load_profile_from_toml

AUDIO_TOML_PATH = Path("src/storyline/config/audio.toml")


class TestAudioTomlConfig:
    @pytest.fixture(scope="class")
    def toml_data(self):
        with AUDIO_TOML_PATH.open("rb") as f:
            return tomllib.load(f)

    # -- TOML structure --

    def test_has_voices_section(self, toml_data):
        voices = toml_data.get("voices", {})
        assert voices, "Missing [voices] section"

    def test_has_profiles_section(self, toml_data):
        profiles = toml_data.get("profiles", {})
        assert profiles, "Missing [profiles] section"

    def test_voice_keys(self, toml_data):
        voices = toml_data["voices"]
        assert len(voices) >= 2, f"Expected at least 2 voices, got {list(voices.keys())}"

    def test_profile_keys(self, toml_data):
        profiles = toml_data["profiles"]
        assert "default" in profiles, "Missing default profile"

    # -- Voice entries --

    def test_every_voice_has_mode(self, toml_data):
        for name, voice in toml_data["voices"].items():
            assert "mode" in voice, f"voices.{name} missing 'mode'"
            assert voice["mode"] in ("custom_voice", "voice_clone"), (
                f"voices.{name} unknown mode: {voice['mode']}"
            )

    def test_every_voice_has_language(self, toml_data):
        for name, voice in toml_data["voices"].items():
            assert "language" in voice, f"voices.{name} missing 'language'"

    def test_voice_clone_has_ref_audio_and_ref_text(self, toml_data):
        for name, voice in toml_data["voices"].items():
            if voice.get("mode") == "voice_clone":
                assert "ref_audio" in voice, f"voices.{name} missing 'ref_audio'"
                assert "ref_text" in voice, f"voices.{name} missing 'ref_text'"
                assert Path(voice["ref_audio"]).exists(), (
                    f"voices.{name} ref_audio file not found: {voice['ref_audio']}"
                )

    def test_custom_voice_has_speaker(self, toml_data):
        for name, voice in toml_data["voices"].items():
            if voice.get("mode") == "custom_voice":
                assert "speaker" in voice, f"voices.{name} missing 'speaker'"

    # -- Default profile --

    def test_default_profile_has_sequence(self, toml_data):
        default = toml_data["profiles"]["default"]
        seq = default.get("sequence", [])
        assert seq, "Default profile has empty or missing sequence"

    def test_default_profile_pause_ms_is_numeric(self, toml_data):
        pause_ms = toml_data["profiles"]["default"].get("pause_ms", 300)
        assert isinstance(pause_ms, (int, float)), f"pause_ms is not numeric: {pause_ms!r}"

    def test_sequence_steps_have_required_fields(self, toml_data):
        seq = toml_data["profiles"]["default"]["sequence"]
        for i, step in enumerate(seq):
            assert "lang" in step, f"step[{i}] missing 'lang'"
            assert "voice" in step, f"step[{i}] missing 'voice'"
            assert "speed" in step, f"step[{i}] missing 'speed'"
            assert step["voice"] in toml_data["voices"], (
                f"step[{i}] voice '{step['voice']}' not found in [voices]"
            )

    # -- load_profile_from_toml --

    def test_load_profile_from_toml_default(self):
        sequence_steps, pause_ms, use_instruct = load_profile_from_toml("default")
        assert len(sequence_steps) > 0, "Expected non-empty sequence steps"
        for step in sequence_steps:
            assert "lang" in step
            assert "voice" in step
            assert "voice_name" in step
            assert "speed" in step
        assert isinstance(pause_ms, (int, float))
        assert isinstance(use_instruct, bool)

    def test_load_profile_from_toml_invalid_raises(self):
        with pytest.raises(KeyError):
            load_profile_from_toml("nonexistent_profile")