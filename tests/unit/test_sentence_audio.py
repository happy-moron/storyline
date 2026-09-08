"""Tests for sentence_audio.py — normalized format parser and audio generation."""

import pytest
from pydub import AudioSegment

from storyline.audio.sentence_audio import (
    LANGUAGE_MAP,
    SentenceBlock,
    SentenceFormatError,
    VoiceSpec,
    generate_sentence_audio,
    parse_sentence_format,
)

# ---------------------------------------------------------------------------
# Sample text for reuse
# ---------------------------------------------------------------------------

SIMPLE_TEXT = """\
zh=1
你好。

en=1
Hello."""

TEXT_WITH_INSTRUCT = """\
zh=1
instruct=Speak warmly and slowly
你好，今天天气真好。

en=1
Hello, the weather is really nice today."""

MULTI_SPEAKER = """\
zh=1
你好。

zh=2
你也好。

en=1
Hello.

en=2
Hello to you too."""

TEXT_WITH_BLANK_BETWEEN = """\
zh=1
你好。



en=1
Hello."""


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestParseSentenceFormat:
    def test_empty_text(self):
        assert parse_sentence_format("") == []

    def test_whitespace_only(self):
        assert parse_sentence_format("\n  \n\n") == []

    def test_simple_zh_en(self):
        blocks = parse_sentence_format(SIMPLE_TEXT)
        assert len(blocks) == 2
        assert blocks[0] == SentenceBlock("zh", 1, "", "你好。", 2)
        assert blocks[1] == SentenceBlock("en", 1, "", "Hello.", 5)

    def test_with_instruct(self):
        blocks = parse_sentence_format(TEXT_WITH_INSTRUCT)
        assert len(blocks) == 2
        assert blocks[0].instruct == "Speak warmly and slowly"
        assert blocks[0].text == "你好，今天天气真好。"
        assert blocks[0].line_number == 3
        assert blocks[1].instruct == ""
        assert blocks[1].text == "Hello, the weather is really nice today."

    def test_multi_speaker(self):
        blocks = parse_sentence_format(MULTI_SPEAKER)
        assert len(blocks) == 4
        assert blocks[0].speaker_id == 1
        assert blocks[1].speaker_id == 2
        assert blocks[2].speaker_id == 1
        assert blocks[3].speaker_id == 2

    def test_multiple_blank_lines(self):
        blocks = parse_sentence_format(TEXT_WITH_BLANK_BETWEEN)
        assert len(blocks) == 2
        assert blocks[0].text == "你好。"
        assert blocks[1].text == "Hello."

    def test_language_property(self):
        blocks = parse_sentence_format(SIMPLE_TEXT)
        assert blocks[0].language == "chinese"
        assert blocks[1].language == "english"

    def test_line_numbers_track_origin(self):
        text = "zh=1\n你好。\n\nen=1\nHello."
        blocks = parse_sentence_format(text)
        assert blocks[0].line_number == 2
        assert blocks[1].line_number == 5

    def test_en_with_high_speaker_id(self):
        blocks = parse_sentence_format("en=42\nSome text.")
        assert blocks[0].lang == "en"
        assert blocks[0].speaker_id == 42

    def test_instruct_with_special_chars(self):
        blocks = parse_sentence_format(
            'zh=1\ninstruct=Speak like a "wise old man" — slow & clear.\n你好。'
        )
        assert blocks[0].instruct == 'Speak like a "wise old man" — slow & clear.'

    def test_instruct_empty_value(self):
        blocks = parse_sentence_format("zh=1\ninstruct=\n你好。")
        assert blocks[0].instruct == ""

    def test_long_text(self):
        long_line = "这是一段非常长的文本" * 20
        blocks = parse_sentence_format(f"zh=1\n{long_line}")
        assert blocks[0].text == long_line

    # -- Error cases --

    def test_invalid_lang_tag(self):
        with pytest.raises(SentenceFormatError, match="Expected 'zh|en="):
            parse_sentence_format("fr=1\nBonjour.")

    def test_non_integer_speaker_id(self):
        with pytest.raises(SentenceFormatError, match="Expected 'zh|en="):
            parse_sentence_format("zh=abc\n你好。")

    def test_missing_text(self):
        with pytest.raises(SentenceFormatError, match="Empty text after header"):
            parse_sentence_format("zh=1\n")

    def test_missing_text_with_instruct(self):
        with pytest.raises(SentenceFormatError, match="Empty text after header"):
            parse_sentence_format("zh=1\ninstruct=whisper\n")

    def test_empty_text_line(self):
        with pytest.raises(SentenceFormatError, match="Empty text"):
            parse_sentence_format("zh=1\n   \n")

    def test_header_instead_of_text(self):
        with pytest.raises(SentenceFormatError, match="Expected text, got another header"):
            parse_sentence_format("zh=1\nen=1\n")

    def test_instruct_after_text(self):
        with pytest.raises(SentenceFormatError, match="Expected 'zh\\|en=") as exc_info:
            parse_sentence_format("zh=1\n你好。\ninstruct=whisper")
        assert exc_info.value.line_number == 3

    def test_garbage_line(self):
        with pytest.raises(SentenceFormatError, match="Expected 'zh|en="):
            parse_sentence_format("garbage\nzh=1\n你好。")


# ---------------------------------------------------------------------------
# LANGUAGE_MAP
# ---------------------------------------------------------------------------


class TestLanguageMap:
    def test_zh_chinese(self):
        assert LANGUAGE_MAP["zh"] == "chinese"

    def test_en_english(self):
        assert LANGUAGE_MAP["en"] == "english"

    def test_raises_on_unknown(self):
        with pytest.raises(KeyError):
            LANGUAGE_MAP["fr"]


# ---------------------------------------------------------------------------
# Fake service for audio generation tests
# ---------------------------------------------------------------------------


class FakeQwen3Service:
    def __init__(self):
        self.builtin_batches = []
        self.clone_batches = []

    def generate_audio_batch(self, texts, languages, speakers, instructs):
        self.builtin_batches.append((texts, languages, speakers, instructs))
        return [AudioSegment.silent(duration=200) for _ in texts]

    def generate_voice_clone_batch(self, texts, languages, ref_audio_path, ref_text):
        self.clone_batches.append((texts, languages, ref_audio_path, ref_text))
        return [AudioSegment.silent(duration=200) for _ in texts]


class FakeOmnivoiceService:
    def __init__(self):
        self.clone_batches = []

    def generate_voice_clone_batch(self, texts, languages, ref_audio_path, ref_text):
        self.clone_batches.append((texts, languages, ref_audio_path, ref_text))
        return [AudioSegment.silent(duration=200) for _ in texts]


# ---------------------------------------------------------------------------
# VoiceSpec resolution tests
# ---------------------------------------------------------------------------


class TestVoiceSpecResolution:
    def test_explicit_builtin(self):
        spec = VoiceSpec(mode="builtin", speaker="Serena")
        assert spec.mode == "builtin"
        assert spec.speaker == "Serena"

    def test_explicit_clone(self):
        spec = VoiceSpec(mode="clone", ref_audio="/tmp/ref.wav", ref_text="你好")
        assert spec.mode == "clone"
        assert spec.ref_audio == "/tmp/ref.wav"

    def test_empty_mode_falls_back(self):
        spec = VoiceSpec(mode="", speaker="Ryan")
        assert spec.mode == ""

    def test_defaults(self):
        spec = VoiceSpec()
        assert spec.mode == "builtin"
        assert spec.speaker == ""
        assert spec.ref_audio == ""
        assert spec.ref_text == ""


# ---------------------------------------------------------------------------
# generate_sentence_audio tests (with monkeypatched service imports)
# ---------------------------------------------------------------------------


class TestGenerateSentenceAudio:
    def test_empty_blocks(self):
        result = generate_sentence_audio([], {}, engine="qwen3", mode="builtin")
        assert result == []

    @pytest.fixture
    def qwen3_fake(self, monkeypatch):
        fake = FakeQwen3Service()
        monkeypatch.setattr(
            "storyline.audio.sentence_audio.Qwen3TTSService",
            lambda **kw: fake,
        )
        return fake

    @pytest.fixture
    def omnivoice_fake(self, monkeypatch):
        fake = FakeOmnivoiceService()
        monkeypatch.setattr(
            "storyline.audio.sentence_audio.OmnivoiceTTSService",
            lambda **kw: fake,
        )
        return fake

    def test_builtin_single_speaker(self, qwen3_fake):
        blocks = parse_sentence_format(SIMPLE_TEXT)
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="Serena")}

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
        )

        assert len(result) == 2
        assert len(qwen3_fake.builtin_batches) == 1
        texts, langs, speakers, instructs = qwen3_fake.builtin_batches[0]
        assert texts == ["你好。", "Hello."]
        assert langs == ["chinese", "english"]
        assert speakers == ["Serena", "Serena"]
        assert instructs == ["", ""]

    def test_builtin_with_instruct(self, qwen3_fake):
        blocks = parse_sentence_format(TEXT_WITH_INSTRUCT)
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="Serena")}

        generate_sentence_audio(blocks, speaker_map, engine="qwen3", mode="builtin")

        assert len(qwen3_fake.builtin_batches) == 2
        # Two groups: one with instruct, one without
        instruct_texts = []
        noinstruct_texts = []
        for texts, _, _, instructs in qwen3_fake.builtin_batches:
            for t, i in zip(texts, instructs):
                if i:
                    instruct_texts.append(t)
                else:
                    noinstruct_texts.append(t)

        assert "你好，今天天气真好。" in instruct_texts
        assert "Hello, the weather is really nice today." in noinstruct_texts

    def test_builtin_mode_from_arg(self, qwen3_fake):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="", speaker="Serena")}  # empty mode

        generate_sentence_audio(blocks, speaker_map, engine="qwen3", mode="builtin")
        assert len(qwen3_fake.builtin_batches) == 1

    def test_clone_mode_from_arg(self, qwen3_fake):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="", ref_audio="/tmp/x.wav", ref_text="hi")}

        generate_sentence_audio(blocks, speaker_map, engine="qwen3", mode="clone")
        assert len(qwen3_fake.clone_batches) == 1

    def test_builtin_missing_speaker_raises(self):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="")}
        with pytest.raises(SentenceFormatError, match="builtin mode requires a speaker"):
            generate_sentence_audio(blocks, speaker_map, engine="qwen3")

    def test_clone_missing_ref_raises(self):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="clone", ref_audio="", ref_text="")}
        with pytest.raises(SentenceFormatError, match="clone mode requires ref_audio"):
            generate_sentence_audio(blocks, speaker_map, engine="qwen3")

    def test_unknown_mode_raises(self):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="unknown_mode")}
        with pytest.raises(SentenceFormatError, match="unknown mode"):
            generate_sentence_audio(blocks, speaker_map, engine="qwen3")

    def test_multi_speaker_batching(self, qwen3_fake):
        blocks = parse_sentence_format(MULTI_SPEAKER)
        speaker_map = {
            1: VoiceSpec(mode="builtin", speaker="Serena"),
            2: VoiceSpec(mode="builtin", speaker="Ryan"),
        }

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
        )

        assert len(result) == 4
        assert len(qwen3_fake.builtin_batches) == 2

        # Should be two batches: all speaker=1 lines, all speaker=2 lines
        all_speakers_used = set()
        for texts, _, speakers, _ in qwen3_fake.builtin_batches:
            all_speakers_used.update(speakers)
        assert all_speakers_used == {"Serena", "Ryan"}

    def test_clone_batching(self, qwen3_fake):
        blocks = parse_sentence_format(MULTI_SPEAKER)
        speaker_map = {
            1: VoiceSpec(mode="clone", ref_audio="a.wav", ref_text="hello"),
            2: VoiceSpec(mode="clone", ref_audio="b.wav", ref_text="hi"),
        }

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="clone",
        )

        assert len(result) == 4
        assert len(qwen3_fake.clone_batches) == 2
        ref_audios = {batch[2] for batch in qwen3_fake.clone_batches}
        assert ref_audios == {"a.wav", "b.wav"}

    def test_mixed_mode_batching(self, qwen3_fake):
        blocks = parse_sentence_format(MULTI_SPEAKER)
        speaker_map = {
            1: VoiceSpec(mode="builtin", speaker="Serena"),
            2: VoiceSpec(mode="clone", ref_audio="b.wav", ref_text="hi"),
        }

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
        )

        assert len(result) == 4
        assert len(qwen3_fake.builtin_batches) == 1
        assert len(qwen3_fake.clone_batches) == 1

    def test_result_order_preserved(self, qwen3_fake):
        blocks = parse_sentence_format(MULTI_SPEAKER)
        speaker_map = {
            1: VoiceSpec(mode="builtin", speaker="Serena"),
            2: VoiceSpec(mode="builtin", speaker="Ryan"),
        }

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
        )

        assert result[0][0].speaker_id == 1
        assert result[0][0].lang == "zh"
        assert result[1][0].speaker_id == 2
        assert result[1][0].lang == "zh"
        assert result[2][0].speaker_id == 1
        assert result[2][0].lang == "en"
        assert result[3][0].speaker_id == 2
        assert result[3][0].lang == "en"

    def test_audio_durations(self, qwen3_fake):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="Serena")}

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
        )

        assert len(result) == 1
        _, audio = result[0]
        assert len(audio) == 200  # FakeService produces 200ms

    def test_omnivoice_engine(self, omnivoice_fake):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="clone", ref_audio="x.wav", ref_text="hi")}

        result = generate_sentence_audio(
            blocks, speaker_map, engine="omnivoice", mode="clone",
        )

        assert len(result) == 1
        assert len(omnivoice_fake.clone_batches) == 1
        texts, langs, ref_audio, ref_text = omnivoice_fake.clone_batches[0]
        assert texts == ["你好。"]
        assert langs == ["chinese"]

    def test_unknown_engine_raises(self):
        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="Serena")}
        with pytest.raises(ValueError, match="Unknown audio engine"):
            generate_sentence_audio(blocks, speaker_map, engine="nonexistent")

    def test_service_manager_qwen3_starts_tts(self, qwen3_fake, monkeypatch):
        svc_calls = {"stopped": [], "started": []}

        class FakeSvcMgr:
            def stop_if_running(self, name):
                svc_calls["stopped"].append(name)
            def start_if_needed(self, name):
                svc_calls["started"].append(name)

        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="Serena")}

        generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
            service_manager=FakeSvcMgr(),
        )

        assert "llm" in svc_calls["stopped"]
        assert "tts" in svc_calls["started"]

    def test_service_manager_omnivoice_stops_all(self, omnivoice_fake):
        svc_calls = {"stopped": [], "started": []}

        class FakeSvcMgr:
            def stop_if_running(self, name):
                svc_calls["stopped"].append(name)
            def start_if_needed(self, name):
                svc_calls["started"].append(name)

        blocks = parse_sentence_format("zh=1\n你好。")
        speaker_map = {1: VoiceSpec(mode="clone", ref_audio="x.wav", ref_text="hi")}

        generate_sentence_audio(
            blocks, speaker_map, engine="omnivoice", mode="clone",
            service_manager=FakeSvcMgr(),
        )

        assert "llm" in svc_calls["stopped"]
        assert "tts" in svc_calls["stopped"]
        assert svc_calls["started"] == []


# ---------------------------------------------------------------------------
# Batching boundary tests
# ---------------------------------------------------------------------------


class TestBatching:
    def test_builtin_splits_at_batch_boundary(self, monkeypatch):
        monkeypatch.setattr(
            "storyline.audio.sentence_audio._QWEN3_BATCH_SIZE", 3,
        )
        monkeypatch.setattr(
            "storyline.audio.sentence_audio.Qwen3TTSService",
            lambda **kw: FakeQwen3Service(),
        )

        lines = []
        for i in range(5):
            lines.append(f"zh=1\n句子{i}。\n")
        text = "\n".join(lines)
        blocks = parse_sentence_format(text)
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="Serena")}

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
        )

        assert len(result) == 5

    def test_clone_splits_at_batch_boundary(self, monkeypatch):
        monkeypatch.setattr(
            "storyline.audio.sentence_audio._QWEN3_BATCH_SIZE", 2,
        )
        monkeypatch.setattr(
            "storyline.audio.sentence_audio.Qwen3TTSService",
            lambda **kw: FakeQwen3Service(),
        )

        lines = []
        for i in range(3):
            lines.append(f"zh=1\n句子{i}。\n")
        text = "\n".join(lines)
        blocks = parse_sentence_format(text)
        speaker_map = {1: VoiceSpec(mode="clone", ref_audio="x.wav", ref_text="hi")}

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="clone",
        )
        assert len(result) == 3


# ---------------------------------------------------------------------------
# Roundtrip — parse + generate with identical blocks
# ---------------------------------------------------------------------------


class TestRoundtrip:
    def test_identical_blocks_dedup_correctly(self, monkeypatch):
        monkeypatch.setattr(
            "storyline.audio.sentence_audio.Qwen3TTSService",
            lambda **kw: FakeQwen3Service(),
        )

        text = "zh=1\n你好。\n\nzh=1\n你好。"
        blocks = parse_sentence_format(text)
        speaker_map = {1: VoiceSpec(mode="builtin", speaker="Serena")}

        result = generate_sentence_audio(
            blocks, speaker_map, engine="qwen3", mode="builtin",
        )

        assert len(result) == 2