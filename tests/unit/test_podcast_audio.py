import json
from pathlib import Path

import pytest
from pydub import AudioSegment

from storyline.podcast.audio_gen import (
    build_instruct,
    generate_dialogue_audio,
    generate_voice_design,
    serialize_profile,
    voice_profile_stem,
)
from storyline.podcast.script_parser import DialogueLine, PodcastScript, VoiceProfile


def _profile(speaker=1, lang="zh", **overrides):
    fields = dict(
        speaker=speaker,
        lang=lang,
        gender="Male",
        age="三十岁",
        pitch="中音域",
        pace="适中",
        volume="中等",
        clarity="清晰",
        fluency="流利",
        timbre="明亮",
        emotion="热情",
        usecase="一个年轻人",
        dialogue="你好吗？我很好。",
    )
    fields.update(overrides)
    return VoiceProfile(**fields)


def _script(lines, profiles):
    return PodcastScript(
        intro=[],
        dialogue=lines,
        breakdown=[],
        outro=[],
        voice_profiles=profiles,
    )


class FakeService:
    def __init__(self, duration_ms=200):
        self.duration_ms = duration_ms
        self.design_calls = []
        self.clone_calls = []

    def voice_design(self, text, language, instruct=""):
        self.design_calls.append((text, language, instruct))
        return AudioSegment.silent(duration=self.duration_ms)

    def generate_voice_clone(self, text, language, ref_audio_path, ref_text):
        self.clone_calls.append((text, language, ref_audio_path, ref_text))
        return AudioSegment.silent(duration=self.duration_ms)

    def generate_voice_clone_batch(self, texts, languages, ref_audio_path, ref_text):
        n = len(texts)
        if isinstance(languages, str):
            languages = [languages] * n
        results = []
        for text, lang in zip(texts, languages):
            self.clone_calls.append((text, lang, ref_audio_path, ref_text))
            results.append(AudioSegment.silent(duration=self.duration_ms))
        return results


class TestVoiceProfileStem:
    def test_underscores_replaced(self):
        assert voice_profile_stem("at-the-gym", 1) == "at_the_gym_1"

    def test_multiple_words(self):
        assert voice_profile_stem("talking-about-last-nights-x", 12) == \
            "talking_about_last_nights_x_12"

    def test_no_hyphens(self):
        assert voice_profile_stem("gym", 3) == "gym_3"


class TestSerializeProfile:
    def test_contains_speaker_unquoted(self):
        text = serialize_profile(_profile(speaker=2))
        assert "speaker = 2" in text

    def test_contains_quoted_fields(self):
        text = serialize_profile(_profile())
        assert 'lang = "zh"' in text
        assert 'gender = "Male"' in text
        assert 'dialogue = "你好吗？我很好。"' in text

    def test_fields_ordered_with_dialogue_last(self):
        text = serialize_profile(_profile())
        lines = text.strip().split("\n")
        assert lines[0] == "speaker = 1"
        assert lines[-1].startswith('dialogue = "')


class TestBuildInstruct:
    def test_zh_maps_keys_to_mandarin(self):
        instruct = build_instruct(_profile(lang="zh"))
        assert "性别: Male" in instruct
        assert "年龄: 三十岁" in instruct
        assert "gender:" not in instruct

    def test_en_keeps_english_keys(self):
        instruct = build_instruct(_profile(lang="en"))
        assert "gender: Male" in instruct
        assert "age: 三十岁" in instruct
        assert "性别" not in instruct

    def test_excludes_dialogue_and_speaker(self):
        instruct = build_instruct(_profile())
        assert "dialogue" not in instruct
        assert "speaker" not in instruct
        assert "lang" not in instruct


class TestGenerateVoiceDesign:
    def test_writes_txt_and_wav(self, tmp_path):
        service = FakeService()
        profile = _profile(speaker=1, lang="zh")

        txt_path, wav_path = generate_voice_design(
            service, profile, "at-the-gym", tmp_path,
        )

        assert txt_path == tmp_path / "at_the_gym_1.txt"
        assert wav_path == tmp_path / "at_the_gym_1.wav"
        assert txt_path.exists()
        assert wav_path.exists()

        assert len(service.design_calls) == 1
        text, language, instruct = service.design_calls[0]
        assert text == profile.dialogue
        assert language == "chinese"
        assert "性别" in instruct

    def test_skips_wav_when_present(self, tmp_path):
        wav_path = tmp_path / "at_the_gym_1.wav"
        AudioSegment.silent(duration=100).export(wav_path, format="wav")

        service = FakeService()
        generate_voice_design(service, _profile(speaker=1), "at-the-gym", tmp_path)

        assert service.design_calls == []


class TestGenerateDialogueAudio:
    def _setup(self, tmp_path):
        slug = "at-the-gym"
        profiles = {
            1: _profile(speaker=1, lang="zh"),
            2: _profile(speaker=2, lang="zh", gender="Female", dialogue="真的吗？"),
        }
        lines = [
            DialogueLine(speaker_id=1, chinese="你好。", english="Hello."),
            DialogueLine(speaker_id=2, chinese="你好吗？", english="How are you?"),
            DialogueLine(speaker_id=1, chinese="我很好。", english="I'm fine."),
        ]
        script = _script(lines, profiles)

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        chunk_json = chunks_dir / f"{slug}_1.json"
        chunk_json.write_text(
            json.dumps({"chunks": [
                {"instruct": "", "instruct_zh": "", "line_range": [0, 0]},
                {"instruct": "", "instruct_zh": "", "line_range": [1, 1]},
                {"instruct": "", "instruct_zh": "", "line_range": [2, 2]},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )
        return slug, script, tmp_path

    def test_generates_voice_design_per_speaker(self, tmp_path):
        slug, script, base = self._setup(tmp_path)
        service = FakeService()

        generate_dialogue_audio(
            script, slug, base, service, voices_dir=tmp_path / "voices",
        )

        assert len(service.design_calls) == 2
        assert (tmp_path / "voices" / "at_the_gym_1.wav").exists()
        assert (tmp_path / "voices" / "at_the_gym_2.wav").exists()

    def test_generates_clone_per_line(self, tmp_path):
        slug, script, base = self._setup(tmp_path)
        service = FakeService()

        generate_dialogue_audio(
            script, slug, base, service, voices_dir=tmp_path / "voices",
        )

        # Speaker-grouped batching: speaker 1 lines first, then speaker 2
        assert len(service.clone_calls) == 3

        texts = {call[0] for call in service.clone_calls}
        assert texts == {"你好。", "你好吗？", "我很好。"}

        ref_audio_1 = str(tmp_path / "voices" / "at_the_gym_1.wav")
        ref_audio_2 = str(tmp_path / "voices" / "at_the_gym_2.wav")

        # Speaker 1 batch: two lines share ref_audio_1
        s1_calls = [c for c in service.clone_calls if c[2] == ref_audio_1]
        assert len(s1_calls) == 2
        assert {c[0] for c in s1_calls} == {"你好。", "我很好。"}
        for call in s1_calls:
            assert call[1] == "chinese"
            assert call[3] == script.voice_profiles[1].dialogue

        # Speaker 2 batch: one line with ref_audio_2
        s2_calls = [c for c in service.clone_calls if c[2] == ref_audio_2]
        assert len(s2_calls) == 1
        assert s2_calls[0][0] == "你好吗？"
        assert s2_calls[0][1] == "chinese"
        assert s2_calls[0][3] == script.voice_profiles[2].dialogue

    def test_writes_audio_files_and_updates_chunk_json(self, tmp_path):
        slug, script, base = self._setup(tmp_path)
        service = FakeService()

        generate_dialogue_audio(
            script, slug, base, service, voices_dir=tmp_path / "voices",
        )

        audio_dir = base / "audio"
        for ci in range(3):
            assert (audio_dir / f"{ci:03d}_{slug}_1_zh.mp3").exists()

        chunk_json = json.loads((base / "chunks" / f"{slug}_1.json").read_text())
        assert len(chunk_json["chunks"]) == 3
        for ci, chunk in enumerate(chunk_json["chunks"]):
            assert chunk["audio_zh"] == f"{ci:03d}_{slug}_1_zh.mp3"
            assert chunk["line_range"] == [ci, ci]
            assert chunk["lines"][0]["start_zh"] == 0.0
            assert chunk["lines"][0]["end_zh"] == 0.2

    def test_idempotent_skips_existing_audio(self, tmp_path):
        slug, script, base = self._setup(tmp_path)
        service = FakeService()

        generate_dialogue_audio(
            script, slug, base, service, voices_dir=tmp_path / "voices",
        )
        first_clones = list(service.clone_calls)

        second_service = FakeService()
        generate_dialogue_audio(
            script, slug, base, second_service, voices_dir=tmp_path / "voices",
        )

        assert second_service.clone_calls == []
        assert second_service.design_calls == []
        assert len(first_clones) == 3
