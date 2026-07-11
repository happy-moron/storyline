"""Integration tests for the TTS service (requires qwentts running)."""

import base64
import json

import pytest
import requests

from storyline.services.manager import ServiceManager, ServiceStatus
from storyline.audio.audiobook_gen_base import load_profile_from_toml
from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService, generate_tts_audio

TTS_URL = "http://127.0.0.1:11433"


@pytest.mark.integration
class TestTTSService:
    @pytest.fixture(scope="class")
    def manager(self):
        mgr = ServiceManager()
        return mgr

    @pytest.fixture(scope="class")
    def ensure_tts(self, manager):
        status = manager.get_status("tts")
        if status == ServiceStatus.OFFLINE:
            llm_status = manager.get_status("llm")
            if llm_status == ServiceStatus.ONLINE:
                manager.stop("llm")
            manager.start("tts")
        yield
        # Leave TTS running for subsequent tests

    # -- Health & Models --

    def test_health_check(self, ensure_tts):
        resp = requests.get(f"{TTS_URL}/health", timeout=5)
        assert resp.status_code == 200

    def test_list_models(self, ensure_tts):
        resp = requests.get(f"{TTS_URL}/models", timeout=5)
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, (list, dict))

    # -- Custom Voice Generation --

    def test_custom_voice_single_english(self, ensure_tts):
        payload = {
            "text": "Hello world",
            "language": "English",
            "speaker": "Ryan",
            "instruct": "",
        }
        resp = requests.post(f"{TTS_URL}/custom_voice", json=payload, timeout=30)
        assert resp.status_code == 200
        audio = base64.b64decode(resp.json()["audio"])
        assert len(audio) > 200

    def test_custom_voice_single_chinese(self, ensure_tts):
        payload = {
            "text": "你好世界",
            "language": "Chinese",
            "speaker": "Vivian",
            "instruct": "",
        }
        resp = requests.post(f"{TTS_URL}/custom_voice", json=payload, timeout=30)
        assert resp.status_code == 200
        audio = base64.b64decode(resp.json()["audio"])
        assert len(audio) > 200

    def test_custom_voice_batch(self, ensure_tts):
        payload = {
            "text": ["Hello world", "How are you?"],
            "language": ["English", "English"],
            "speaker": ["Ryan", "Ryan"],
            "instruct": ["", ""],
        }
        resp = requests.post(f"{TTS_URL}/custom_voice", json=payload, timeout=60)
        assert resp.status_code == 200
        audio_data = resp.json().get("audio", [])
        assert len(audio_data) == 2

    # -- Qwen3TTSService class --

    def test_qwen3_service_english(self, ensure_tts):
        service = Qwen3TTSService()
        audio = service.generate_audio("Hello world", "english", "")
        assert len(audio) > 200

    def test_qwen3_service_chinese(self, ensure_tts):
        service = Qwen3TTSService()
        audio = service.generate_audio("你好世界", "chinese", "")
        assert len(audio) > 200

    def test_generate_tts_audio_chinese(self, ensure_tts):
        service = Qwen3TTSService()
        ref_audio, ref_text, _, _ = load_profile_from_toml("default")
        audio = generate_tts_audio(service, "测试语音", ref_audio, ref_text, is_chinese=True)
        assert len(audio) > 200

    def test_generate_tts_audio_english(self, ensure_tts):
        service = Qwen3TTSService()
        ref_audio, ref_text, _, _ = load_profile_from_toml("default")
        audio = generate_tts_audio(service, "Test voice", ref_audio, ref_text, is_chinese=False)
        assert len(audio) > 200

    # -- Speaker mapping --

    def test_speaker_mapping(self):
        service = Qwen3TTSService()
        cases = [
            ("chinese", "Vivian"),
            ("zh", "Vivian"),
            ("zh-cn", "Vivian"),
            ("english", "Ryan"),
            ("en", "Ryan"),
            ("en-us", "Ryan"),
            ("french", "Ryan"),  # unknown → default
        ]
        for lang_code, expected in cases:
            actual = service._get_speaker_for_lang(lang_code)
            assert actual == expected, f"lang '{lang_code}' → '{actual}', expected '{expected}'"