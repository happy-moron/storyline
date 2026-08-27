import json
from pathlib import Path

import pytest
from pydub import AudioSegment

from storyline.podcast.mp3_gen import (
    CharacterStep,
    HostStep,
    _prefetch_character_audio,
    _prefetch_host_audio,
    assemble_podcast_mp3,
    build_podcast_sequence,
    build_podcast_tags,
    dialogue_index_map,
    host_reference,
    read_profile_dialogue,
    render_step,
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
# Voice profile file parsing
# ---------------------------------------------------------------------------

class TestReadProfileDialogue:
    def test_extracts_dialogue(self, tmp_path):
        path = tmp_path / "teacher.txt"
        path.write_text(
            'lang = "zh"\ngender = "女"\ndialogue = "你学得很快。"\n',
            encoding="utf-8",
        )
        assert read_profile_dialogue(path) == "你学得很快。"

    def test_handles_curly_quotes(self, tmp_path):
        path = tmp_path / "teacher.txt"
        path.write_text(
            'dialogue = “你学得很快。现在，我们来试一点。”\n',
            encoding="utf-8",
        )
        assert read_profile_dialogue(path) == "你学得很快。现在，我们来试一点。"

    def test_missing_dialogue_raises(self, tmp_path):
        path = tmp_path / "bad.txt"
        path.write_text('lang = "zh"\n', encoding="utf-8")
        with pytest.raises(ValueError, match="dialogue"):
            read_profile_dialogue(path)


class TestHostReference:
    def test_teacher_paths(self, tmp_path):
        (tmp_path / "teacher.txt").write_text('dialogue = "老师。"', encoding="utf-8")
        (tmp_path / "teacher.wav").write_bytes(b"wav")
        wav, ref_text = host_reference("teacher", tmp_path)
        assert wav == tmp_path / "teacher.wav"
        assert ref_text == "老师。"

    def test_student_paths(self, tmp_path):
        (tmp_path / "student.txt").write_text('dialogue = "Hi there."', encoding="utf-8")
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

        assert steps[0] == HostStep("teacher", "en", "Welcome, everyone.")
        assert steps[1] == HostStep("student", "en", "Hi! I'm Mark.")

        # Dialogue, three full passes
        for rep in range(3):
            base = 2 + rep * 2
            assert steps[base] == CharacterStep(1, "你好。", 0)
            assert steps[base + 1] == CharacterStep(2, "你好吗？", 1)

        # Line by line: teacher zh + student en, three times per line
        llb_start = 2 + 3 * 2
        expected_pairs = [
            ("teacher", "zh", "你好。"),
            ("student", "en", "Hello."),
        ] * 3 + [
            ("teacher", "zh", "你好吗？"),
            ("student", "en", "How are you?"),
        ] * 3
        for offset, (speaker, lang, text) in enumerate(expected_pairs):
            assert steps[llb_start + offset] == HostStep(speaker, lang, text)

        # Breakdown: host line then quoted dialogue (matched to index 0)
        bd_start = llb_start + len(expected_pairs)
        assert steps[bd_start] == HostStep("teacher", "en", "Let's break it down.")
        assert steps[bd_start + 1] == CharacterStep(1, "你好。", 0)

        # Outro
        assert steps[bd_start + 2] == HostStep("teacher", "en", "That's all for today.")
        assert steps[bd_start + 3] == HostStep("student", "en", "See you next time!")

        assert len(steps) == 24

    def test_custom_repetition_counts(self):
        steps = build_podcast_sequence(_script(), dialogue_repetitions=2,
                                       line_by_line_repetitions=1)
        # intro(2) + dialogue(2*2) + line-by-line(2*1*2) + breakdown(2) + outro(2)
        assert len(steps) == 2 + 4 + 4 + 2 + 2

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
# Rendering steps
# ---------------------------------------------------------------------------

class TestRenderStep:
    def test_host_step_clones(self, tmp_path):
        (tmp_path / "teacher.txt").write_text('dialogue = "老师。"', encoding="utf-8")
        (tmp_path / "teacher.wav").write_bytes(b"wav")
        service = FakeService()

        render_step(
            HostStep("teacher", "zh", "你好。"), _script(), "slug", tmp_path,
            service, tmp_path,
        )

        assert len(service.clone_calls) == 1
        text, language, ref_audio, ref_text = service.clone_calls[0]
        assert text == "你好。"
        assert language == "chinese"
        assert ref_audio == str(tmp_path / "teacher.wav")
        assert ref_text == "老师。"

    def test_character_step_reuses_existing_audio(self, tmp_path):
        audio_dir = tmp_path / "audio"
        audio_dir.mkdir()
        reuse = audio_dir / "000_slug_1_zh.mp3"
        AudioSegment.silent(duration=150).export(reuse, format="mp3")

        service = FakeService()
        seg = render_step(
            CharacterStep(1, "你好。", 0), _script(), "slug", tmp_path,
            service, tmp_path,
        )

        assert service.clone_calls == []
        assert len(seg) == 150

    def test_character_step_generates_when_missing(self, tmp_path):
        service = FakeService(duration_ms=120)
        render_step(
            CharacterStep(1, "你好。", 0), _script(), "slug", tmp_path,
            service, tmp_path,
        )

        assert len(service.clone_calls) == 1
        text, language, ref_audio, ref_text = service.clone_calls[0]
        assert text == "你好。"
        assert language == "chinese"
        assert ref_audio == str(tmp_path / "slug_1.wav")
        assert ref_text == "你好。"


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

class TestAssemblePodcastMp3:
    def _setup(self, tmp_path):
        voices_dir = tmp_path / "voices"
        voices_dir.mkdir()
        (voices_dir / "teacher.txt").write_text('dialogue = "老师。"', encoding="utf-8")
        (voices_dir / "teacher.wav").write_bytes(b"wav")
        (voices_dir / "student.txt").write_text('dialogue = "Hi there."', encoding="utf-8")
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

        # Character dialogue audio for both lines (full + breakdown) is reused;
        # only unique hosts (intro/outro/breakdown-host/line-by-line) are cloned.
        # unique hosts: intro(2) + line-by-line(4 unique) + breakdown host(1) + outro(2) = 9
        assert len(service.clone_calls) == 9

    def test_clones_when_no_reader_audio(self, tmp_path):
        voices_dir = tmp_path / "voices"
        voices_dir.mkdir()
        (voices_dir / "teacher.txt").write_text('dialogue = "老师。"', encoding="utf-8")
        (voices_dir / "teacher.wav").write_bytes(b"wav")
        (voices_dir / "student.txt").write_text('dialogue = "Hi."', encoding="utf-8")
        (voices_dir / "student.wav").write_bytes(b"wav")
        base_dir = tmp_path / "books" / "podcasts" / "slug"
        output_path = tmp_path / "out" / "slug.mp3"

        service = FakeService()
        assemble_podcast_mp3(
            _script(), "slug", service, base_dir, output_path,
            voices_dir=voices_dir, pause_ms=0,
        )

        # Batch groups: 5 teacher host + 4 student host + 1 speaker1 char + 1 speaker2 char
        assert len(service.clone_calls) == 11
        assert output_path.exists()


class TestBuildPodcastTags:
    def test_tags(self):
        tags = build_podcast_tags("talking-about-last-nights-soccer-results")
        assert tags["title"] == "talking-about-last-nights-soccer-results"
        assert tags["artist"] == "podcasts"
        assert tags["album"] == "podcasts"

    def test_custom_author(self):
        tags = build_podcast_tags("ep", author="custom")
        assert tags["artist"] == "custom"
