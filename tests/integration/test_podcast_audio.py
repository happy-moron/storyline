import json

import pytest

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.podcast.audio_gen import generate_dialogue_audio
from storyline.podcast.script_parser import DialogueLine, PodcastScript, VoiceProfile
from storyline.services.manager import ServiceManager, ServiceStatus


def _profile(speaker, gender, dialogue):
    return VoiceProfile(
        speaker=speaker,
        lang="zh",
        gender=gender,
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


@pytest.mark.integration
class TestPodcastAudioGen:
    @pytest.fixture(scope="class")
    def ensure_tts(self):
        mgr = ServiceManager()
        if mgr.get_status("tts") == ServiceStatus.OFFLINE:
            if mgr.get_status("llm") == ServiceStatus.ONLINE:
                mgr.stop("llm")
            mgr.start("tts")
        yield

    def test_voice_design(self, ensure_tts):
        service = Qwen3TTSService()
        audio = service.voice_design("你好吗？", "chinese", "性别: 男")
        assert len(audio) > 200

    def test_generate_dialogue_audio(self, ensure_tts, tmp_path):
        script = PodcastScript(
            intro=[],
            outro=[],
            breakdown=[],
            dialogue=[
                DialogueLine(speaker_id=1, chinese="你好。", english="Hello."),
                DialogueLine(speaker_id=2, chinese="你好吗？", english="How are you?"),
            ],
            voice_profiles={
                1: _profile(1, "Male", "你好。我是小王。"),
                2: _profile(2, "Female", "你好吗？很高兴认识你。"),
            },
        )

        chunks_dir = tmp_path / "chunks"
        chunks_dir.mkdir()
        (chunks_dir / "at-the-gym_1.json").write_text(
            json.dumps({"chunks": [
                {"instruct": "", "instruct_zh": "", "line_range": [0, 0]},
                {"instruct": "", "instruct_zh": "", "line_range": [1, 1]},
            ]}, ensure_ascii=False),
            encoding="utf-8",
        )

        n = generate_dialogue_audio(
            script, "at-the-gym", tmp_path,
            voices_dir=tmp_path / "voices",
        )

        assert n == 2
        audio_dir = tmp_path / "audio"
        assert (audio_dir / "000_at-the-gym_1_zh.mp3").exists()
        assert (audio_dir / "001_at-the-gym_1_zh.mp3").exists()
        assert (tmp_path / "voices" / "at_the_gym_1.wav").exists()
        assert (tmp_path / "voices" / "at_the_gym_2.wav").exists()

        chunk_data = json.loads((chunks_dir / "at-the-gym_1.json").read_text())
        assert chunk_data["chunks"][0]["audio_zh"] == "000_at-the-gym_1_zh.mp3"
        assert chunk_data["chunks"][0]["lines"][0]["end_zh"] > 0
