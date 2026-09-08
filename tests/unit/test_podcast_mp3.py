import json
from pathlib import Path

import pytest
from pydub import AudioSegment

from storyline.podcast.mp3_gen import (
    _prefetch_character_audio,
    _prefetch_host_audio,
    assemble_podcast_mp3,
    assemble_flashcard_vocab_mp3,
    build_podcast_tags,
    host_reference,
    HostVoiceConfig,
)
from storyline.podcast.steps import (
    build_flashcard_intro_sequence,
    build_podcast_sequence,
    CharacterStep,
    dialogue_index_map,
    HostStep,
)
from storyline.podcast.script_parser import (
    DialogueLine,
    HostLine,
    PodcastScript,
    VoiceProfile,
)


def _profile(speaker=1, lang="zh", dialogue="你好吗？我很好。", **overrides):
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
        dialogue=dialogue,
    )
    fields.update(overrides)
    return VoiceProfile(**fields)


def _script():
    return PodcastScript(
        intro=[
            HostLine(lang="en", speaker="teacher", text="Welcome, everyone."),
            HostLine(lang="en", speaker="student", text="Hi! I'm Mark."),
        ],
        dialogue=[
            DialogueLine(speaker_id=1, chinese="你好。", english="Hello."),
            DialogueLine(speaker_id=2, chinese="你好吗？", english="How are you?"),
        ],
        breakdown=[
            HostLine(lang="en", speaker="teacher", text="Let's break it down."),
            DialogueLine(speaker_id=1, chinese="你好。", english="Hello."),
        ],
        outro=[
            HostLine(lang="en", speaker="teacher", text="That's all for today."),
            HostLine(lang="en", speaker="student", text="See you next time!"),
        ],
        voice_profiles={
            1: _profile(speaker=1, lang="zh", dialogue="你好。"),
            2: _profile(speaker=2, lang="zh", dialogue="你好吗？"),
        },
    )


class FakeService:
    def __init__(self, duration_ms=100):
        self.duration_ms = duration_ms
        self.clone_calls = []
        self.audio_calls = []

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

    def generate_audio(self, text, language, speaker, instruct=""):
        self.audio_calls.append((text, language, speaker, instruct))
        return AudioSegment.silent(duration=self.duration_ms)

    def generate_audio_batch(self, texts, languages, speakers, instructs):
        n = len(texts)
        if isinstance(languages, str):
            languages = [languages] * n
        if isinstance(speakers, str):
            speakers = [speakers] * n
        if isinstance(instructs, str):
            instructs = [instructs] * n
        results = []
        for t, l, s, i in zip(texts, languages, speakers, instructs):
            self.audio_calls.append((t, l, s, i))
            results.append(AudioSegment.silent(duration=self.duration_ms))
        return results


# ---------------------------------------------------------------------------
# Host reference
# ---------------------------------------------------------------------------

class TestHostReference:
    def test_teacher_paths(self, tmp_path):
        (tmp_path / "teacher-ref.txt").write_text("老师。", encoding="utf-8")
        (tmp_path / "teacher.wav").write_bytes(b"wav")
        wav, ref_text = host_reference("teacher", tmp_path)
        assert wav == tmp_path / "teacher.wav"
        assert ref_text == "老师。"

    def test_student_paths(self, tmp_path):
        (tmp_path / "student-ref.txt").write_text("Hi there.", encoding="utf-8")
        (tmp_path / "student.wav").write_bytes(b"wav")
        wav, ref_text = host_reference("student", tmp_path)
        assert wav == tmp_path / "student.wav"
        assert ref_text == "Hi there."

    def test_unknown_speaker_raises(self, tmp_path):
        with pytest.raises(ValueError, match="speaker"):
            host_reference("narrator", tmp_path)


# ---------------------------------------------------------------------------
# Sequence building
# ---------------------------------------------------------------------------

class TestBuildPodcastSequence:
    def test_flat_sequence_order(self):
        steps = build_podcast_sequence(_script(), 3, 3)

        def check(actual, expected):
            assert actual == expected

        check(steps[0], HostStep("teacher", "en", "Welcome, everyone."))
        check(steps[1], HostStep("student", "en", "Hi! I'm Mark."))

        # First dialogue section: label + 3 reps with transitions
        idx = 2
        check(steps[idx], HostStep("teacher", "en", "Now we'll hear the dialogue three times"))
        idx += 1
        check(steps[idx], CharacterStep(1, "你好。", 0)); idx += 1
        check(steps[idx], CharacterStep(2, "你好吗？", 1)); idx += 1
        check(steps[idx], HostStep("teacher", "en", "Second time")); idx += 1
        check(steps[idx], CharacterStep(1, "你好。", 0)); idx += 1
        check(steps[idx], CharacterStep(2, "你好吗？", 1)); idx += 1
        check(steps[idx], HostStep("teacher", "en", "Third time")); idx += 1
        check(steps[idx], CharacterStep(1, "你好。", 0)); idx += 1
        check(steps[idx], CharacterStep(2, "你好吗？", 1)); idx += 1

        # Line-by-line: label + 3 reps (no inner transitions in this mode)
        check(steps[idx], HostStep("teacher", "en", "Now we'll hear the dialogue with translation three times"))
        idx += 1
        check(steps[idx], HostStep("teacher", "zh", "你好。")); idx += 1
        check(steps[idx], HostStep("student", "en", "Hello.")); idx += 1
        check(steps[idx], HostStep("teacher", "zh", "你好。")); idx += 1
        check(steps[idx], HostStep("student", "en", "Hello.")); idx += 1
        check(steps[idx], HostStep("teacher", "zh", "你好。")); idx += 1
        check(steps[idx], HostStep("student", "en", "Hello.")); idx += 1
        check(steps[idx], HostStep("teacher", "zh", "你好吗？")); idx += 1
        check(steps[idx], HostStep("student", "en", "How are you?")); idx += 1
        check(steps[idx], HostStep("teacher", "zh", "你好吗？")); idx += 1
        check(steps[idx], HostStep("student", "en", "How are you?")); idx += 1
        check(steps[idx], HostStep("teacher", "zh", "你好吗？")); idx += 1
        check(steps[idx], HostStep("student", "en", "How are you?")); idx += 1

        # Breakdown
        check(steps[idx], HostStep("teacher", "en", "Let's break it down.")); idx += 1
        check(steps[idx], CharacterStep(1, "你好。", 0)); idx += 1

        # Final dialogue section: label + 3 reps
        check(steps[idx], HostStep("teacher", "en", "Let's hear the dialogue three more times")); idx += 1
        check(steps[idx], CharacterStep(1, "你好。" , 0)); idx += 1
        check(steps[idx], CharacterStep(2, "你好吗？", 1)); idx += 1
        check(steps[idx], HostStep("teacher", "en", "Second time")); idx += 1
        check(steps[idx], CharacterStep(1, "你好。", 0)); idx += 1
        check(steps[idx], CharacterStep(2, "你好吗？", 1)); idx += 1
        check(steps[idx], HostStep("teacher", "en" , "Third time")); idx += 1
        check(steps[idx], CharacterStep(1, "你好。", 0)); idx += 1
        check(steps[idx], CharacterStep(2, "你好吗？", 1)); idx += 1

        # Outro
        check(steps[idx], HostStep("teacher", "en", "That's all for today."))
        check(steps[idx + 1], HostStep("student", "en", "See you next time!"))

        assert len(steps) == 37

    def test_custom_repetition_counts(self):
        steps = build_podcast_sequence(_script(), dialogue_repetitions=2,
                                       line_by_line_repetitions=2)
        # intro(2) + first_dialogue(1+2+1+2=6) + line-by-line(1+4+4=9)
        # + breakdown(2) + final_dialogue(1+2+1+2=6) + outro(2)
        assert len(steps) == 2 + 6 + 9 + 2 + 6 + 2

    def test_dialogue_index_map_first_occurrence(self):
        script = PodcastScript(
            intro=[], outro=[], breakdown=[], voice_profiles={},
            dialogue=[
                DialogueLine(speaker_id=1, chinese="你好。", english="Hello."),
                DialogueLine(speaker_id=2, chinese="你好。", english="Hi."),
            ],
        )
        assert dialogue_index_map(script) == {"你好。": 0}


# ---------------------------------------------------------------------------
# Prefetch
# ---------------------------------------------------------------------------

class TestPrefetchHostAudio:
    def test_builtin_hosts_grouped_by_speaker(self, tmp_path):
        host_service = FakeService()
        clone_service = FakeService()
        host_voice = HostVoiceConfig(
            teacher_builtin_speaker="Serena",
            student_builtin_speaker="Ryan",
        )
        steps = [
            HostStep("teacher", "en", "Hello"),
            HostStep("teacher", "en", "Hello"),  # duplicate
            HostStep("student", "en", "Hi"),
        ]
        cache = _prefetch_host_audio(
            steps, host_service, clone_service, tmp_path,
            host_voice, use_builtin_hosts=True,
        )
        assert len(cache) == 2  # unique (speaker, lang, text) pairs
        # Only unique texts get generated
        assert len(host_service.audio_calls) == 2

    def test_clone_hosts_uses_reference(self, tmp_path):
        (tmp_path / "teacher-ref.txt").write_text("老师。", encoding="utf-8")
        (tmp_path / "teacher.wav").write_bytes(b"wav")
        host_service = FakeService()
        clone_service = FakeService()
        host_voice = HostVoiceConfig()
        steps = [HostStep("teacher", "zh", "你好。")]
        cache = _prefetch_host_audio(
            steps, host_service, clone_service, tmp_path,
            host_voice, use_builtin_hosts=False,
        )
        assert len(clone_service.clone_calls) == 1
        assert clone_service.clone_calls[0][0] == "你好。"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

class TestAssemblePodcastMp3:
    def _setup(self, tmp_path):
        voices_dir = tmp_path / "voices"
        voices_dir.mkdir()
        (voices_dir / "teacher-ref.txt").write_text("老师。", encoding="utf-8")
        (voices_dir / "teacher.wav").write_bytes(b"wav")
        (voices_dir / "student-ref.txt").write_text("Hi there.", encoding="utf-8")
        (voices_dir / "student.wav").write_bytes(b"wav")

        base_dir = tmp_path / "books" / "podcasts" / "slug"
        (base_dir / "audio").mkdir(parents=True)
        AudioSegment.silent(duration=50).export(
            base_dir / "audio" / "000_slug_1_zh.mp3", format="mp3",
        )
        AudioSegment.silent(duration=50).export(
            base_dir / "audio" / "001_slug_1_zh.mp3", format="mp3",
        )

        output_path = tmp_path / "temp" / "podcasts" / "slug.mp3"
        return voices_dir, base_dir, output_path

    def test_produces_mp3(self, tmp_path):
        voices_dir, base_dir, output_path = self._setup(tmp_path)
        service = FakeService()

        result = assemble_podcast_mp3(
            _script(), "slug", service, base_dir, output_path,
            voices_dir=voices_dir, pause_ms=0,
        )

        assert result == output_path
        assert output_path.exists()

    def test_reuses_dialogue_audio_for_full_and_breakdown(self, tmp_path):
        voices_dir, base_dir, output_path = self._setup(tmp_path)
        service = FakeService()

        assemble_podcast_mp3(
            _script(), "slug", service, base_dir, output_path,
            voices_dir=voices_dir, pause_ms=0,
        )

        # All character dialogue is reused; only unique hosts are cloned.
        # Unique teachers: intro(1) + 5 transition labels + line-by-line zh(2)
        #   + breakdown(1) + outro(1) = 10
        # Unique students: intro(1) + line-by-line en(2) + outro(1) = 4
        assert len(service.clone_calls) == 14

    def test_clones_when_no_reader_audio(self, tmp_path):
        voices_dir = tmp_path / "voices"
        voices_dir.mkdir()
        (voices_dir / "teacher-ref.txt").write_text("老师。", encoding="utf-8")
        (voices_dir / "teacher.wav").write_bytes(b"wav")
        (voices_dir / "student-ref.txt").write_text("Hi.", encoding="utf-8")
        (voices_dir / "student.wav").write_bytes(b"wav")
        base_dir = tmp_path / "books" / "podcasts" / "slug"
        output_path = tmp_path / "out" / "slug.mp3"

        service = FakeService()
        assemble_podcast_mp3(
            _script(), "slug", service, base_dir, output_path,
            voices_dir=voices_dir, pause_ms=0,
        )

        # Teacher hosts: 10 unique, student hosts: 4 unique,
        # characters: 2 (speaker 1 "你好。" + speaker 2 "你好吗？")
        assert len(service.clone_calls) == 16
        assert output_path.exists()


class TestBuildPodcastTags:
    def test_tags(self):
        tags = build_podcast_tags("talking-about-last-nights-soccer-results")
        assert tags["title"] == "talking-about-last-nights-soccer-results"
        assert tags["artist"] == "podcasts"
        assert tags["album" ] == "podcasts"

    def test_custom_author(self):
        tags = build_podcast_tags("ep", author="custom")
        assert tags["artist"] == "custom"