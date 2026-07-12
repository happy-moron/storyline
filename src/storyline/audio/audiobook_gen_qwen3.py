import argparse
import base64
import io

import requests
from pydub import AudioSegment

from .audiobook_gen_base import process_json_to_audio_common


class Qwen3TTSService:
    """Client for Qwen3-TTS Flask service."""

    def __init__(self, base_url: str | None = None, tts_timeout: int = 300, align_timeout: int = 300):
        if base_url is None:
            base_url = "http://127.0.0.1:11433"
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        self._tts_timeout = tts_timeout
        self._align_timeout = align_timeout

    def _post_and_decode(self, endpoint, payload):
        response = self.session.post(
            f"{self.base_url}{endpoint}",
            json=payload,
            timeout=self._tts_timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(f"TTS {endpoint} failed ({response.status_code}): {response.text}")
        data = response.json()
        audio_b64 = data.get('audio')
        if not audio_b64:
            raise RuntimeError(f"No audio in response: {data}")
        return AudioSegment.from_file(io.BytesIO(base64.b64decode(audio_b64)))

    def generate_audio(self, text, language, speaker, instruct=""):
        return self._post_and_decode("/custom_voice", {
            "text": text,
            "language": language.capitalize(),
            "speaker": speaker,
            "instruct": instruct
        })

    def forced_align(self, audio: AudioSegment, text: str, language: str) -> list[dict]:
        """Run forced alignment on audio with known reference text.

        Returns a list of word dicts: [{"text": "...", "start_time": N, "end_time": N}, ...]
        """
        buf = io.BytesIO()
        audio.export(buf, format="wav")
        buf.seek(0)
        audio_b64 = base64.b64encode(buf.read()).decode("utf-8")

        response = self.session.post(
            f"{self.base_url}/forced_aligner",
            json={"audio": audio_b64, "text": text, "language": language.capitalize()},
            timeout=self._align_timeout,
        )
        if response.status_code != 200:
            raise RuntimeError(
                f"Forced aligner failed ({response.status_code}): {response.text}"
            )
        data = response.json()
        words = data.get("words", [])
        if not words:
            raise RuntimeError(f"No words in forced aligner response: {data}")
        return words

    def generate_voice_clone(self, text, language, ref_audio_path, ref_text):
        with open(ref_audio_path, 'rb') as f:
            ref_audio_b64 = base64.b64encode(f.read()).decode('utf-8')
        return self._post_and_decode("/voice_clone", {
            "text": text,
            "language": language.capitalize(),
            "ref_audio": ref_audio_b64,
            "ref_text": ref_text
        })


def generate_tts_audio(service, text, voice):
    """Dispatch TTS generation based on the voice's mode.

    voice dict fields (from audio.toml):
      mode         - "custom_voice" or "voice_clone"
      language     - "chinese" or "english"
      speaker      - (custom_voice) Qwen3 pretrained speaker name
      instruct     - (custom_voice) optional voice direction
      ref_audio    - (voice_clone) path to reference audio file
      ref_text     - (voice_clone) transcript of the reference audio
    """
    mode = voice["mode"]
    language = voice["language"]

    if mode == "custom_voice":
        return service.generate_audio(
            text, language,
            speaker=voice["speaker"],
            instruct=voice.get("instruct", "")
        )
    elif mode == "voice_clone":
        return service.generate_voice_clone(
            text, language,
            ref_audio_path=voice["ref_audio"],
            ref_text=voice["ref_text"]
        )
    else:
        raise ValueError(f"Unknown voice mode: {mode}")


def process_json_to_audio(input_file, output_file, profile_key="default",
                          standalone_file=None, service=None):
    if service is None:
        service = Qwen3TTSService()

    process_json_to_audio_common(
        input_file, output_file, profile_key, service,
        standalone_file=standalone_file,
        generate_tts_fn=generate_tts_audio
    )


def main():
    parser = argparse.ArgumentParser(description='Convert JSON sentences to audio with specified repetitions.')
    parser.add_argument('--input', required=True, help='Input JSON file path')
    parser.add_argument('--output', required=True, help='Output MP3 file path')
    parser.add_argument('--profile', default='default', help='Profile key from audio.toml')
    args = parser.parse_args()
    process_json_to_audio(args.input, args.output, args.profile)


if __name__ == "__main__":
    main()
