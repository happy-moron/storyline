import shutil
from pathlib import Path

import pytest

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.podcast.audio_gen import generate_voice_design
from storyline.podcast.mp3_gen import assemble_podcast_mp3
from storyline.podcast.script_parser import DialogueLine, HostLine, PodcastScript, VoiceProfile
from storyline.services.manager import ServiceManager, ServiceStatus

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_REAL_VOICES_DIR = _PROJECT_ROOT / "voices"


def _profile(speaker=1):
    return VoiceProfile(
        speaker=speaker,
        lang="zh",
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
        dialogue="你好。我是小王。",
    )


def _script():
    return PodcastScript(
        intro=[
            HostLine(lang="en", speaker="teacher", text="Welcome to the show."),
            HostLine(lang="en", speaker="student", text="Great to be here."),
        ],
        dialogue=[
            DialogueLine(speaker_id=1, chinese="你好。", english="Hello."),
        ],
        breakdown=[
            HostLine(lang="en", speaker="teacher", text="Let's hear that line."),
        ],
        outro=[
            HostLine(lang="en", speaker="teacher", text="Thanks for listening."),
            HostLine(lang="en", speaker="student", text="See you next time."),
        ],
        voice_profiles={1: _profile()},
    )


@pytest.mark.integration
class TestPodcastMp3Gen:
    @pytest.fixture(scope="class")
    def ensure_tts(self):
        mgr = ServiceManager()
        if mgr.get_status("tts") == ServiceStatus.OFFLINE:
            if mgr.get_status("llm") == ServiceStatus.ONLINE:
                mgr.stop("llm")
            mgr.start("tts")
        yield

    def test_assemble_full_mp3(self, ensure_tts, tmp_path):
        voices_dir = tmp_path / "voices"
        voices_dir.mkdir()

        for name in ("teacher", "student"):
            shutil.copy(_REAL_VOICES_DIR / f"{name}.txt", voices_dir / f"{name}.txt")
            shutil.copy(_REAL_VOICES_DIR / f"{name}.wav", voices_dir / f"{name}.wav")

        service = Qwen3TTSService()
        generate_voice_design(service, _profile(), "test-episode", voices_dir)

        base_dir = tmp_path / "books" / "podcasts" / "test-episode"
        output_path = tmp_path / "out" / "test-episode.mp3"

        result = assemble_podcast_mp3(
            _script(), "test-episode", service, base_dir, output_path,
            voices_dir=voices_dir,
        )

        assert result == output_path
        assert output_path.exists()
        assert output_path.stat().st_size > 0
