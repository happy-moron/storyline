"""Generate audio using the TTS voice-design endpoint with a custom instruct.

Usage:
    python scripts/voice_design.py --language en --instruct-file /path/to/instruct.txt
    python scripts/voice_design.py --language zh --instruct-file /path/to/instruct.txt
    python scripts/voice_design.py -l en -i /path/to/instruct.txt -o custom_output.wav
"""

import argparse
import base64
import io
import sys
import time
from pathlib import Path

import requests
from pydub import AudioSegment

# ---------------------------------------------------------------------------
# Project imports — ensure we can resolve storyline
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storyline.services.manager import ServiceManager, ServiceStatus  # noqa: E402

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------
TEXT_FILES = {
    "en": Path.home() / "temp" / "audio" / "clones" / "reference-30s-en.txt",
    "zh": Path.home() / "temp" / "audio" / "clones" / "reference-zh.txt",
}

LANGUAGE_MAP = {
    "en": "English",
    "zh": "Chinese",
}


def _resolve_service_manager() -> ServiceManager:
    """Return a ServiceManager, reusing the singleton pattern it already uses."""
    return ServiceManager()


def _ensure_tts(svc: ServiceManager) -> bool:
    """Ensure the TTS service is running.

    Returns True if it was already running, False if we had to start it.
    """
    was_running = svc.get_status("tts") == ServiceStatus.ONLINE
    if not was_running:
        svc.start("tts")
    return was_running


def _read_text(language: str) -> str:
    """Read the reference text for the given language."""
    path = TEXT_FILES[language]
    if not path.exists():
        raise FileNotFoundError(
            f"Reference text file not found: {path}\n"
            f"Expected at ~/temp/audio/clones/reference-{'30s-en' if language == 'en' else 'zh'}.txt"
        )
    return path.read_text(encoding="utf-8").strip()


def _read_instruct(path: str | Path) -> str:
    """Read the voice-design instruction from a file."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Instruct file not found: {p}")
    return p.read_text(encoding="utf-8").strip()


def _call_voice_design(text: str, language: str, instruct: str,
                       base_url: str = "http://127.0.0.1:11433",
                       timeout: int = 300) -> AudioSegment:
    """Call the /voice_design endpoint and return decoded audio."""
    payload = {
        "text": text,
        "language": LANGUAGE_MAP[language],
        "instruct": instruct,
    }
    response = requests.post(
        f"{base_url}/voice_design",
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Voice design failed ({response.status_code}): {response.text}"
        )
    data = response.json()
    audio_b64 = data.get("audio")
    if not audio_b64:
        raise RuntimeError(f"No audio in response: {data}")
    return AudioSegment.from_file(io.BytesIO(base64.b64decode(audio_b64)))


def main():
    parser = argparse.ArgumentParser(
        description="Generate audio using the TTS voice-design endpoint",
    )
    parser.add_argument(
        "-l", "--language",
        choices=sorted(TEXT_FILES.keys()),
        default="en",
        help=f"Language to generate ({' / '.join(sorted(TEXT_FILES.keys()))}). "
             f"Selects the corresponding reference text file.",
    )
    parser.add_argument(
        "-i", "--instruct-file",
        required=True,
        type=str,
        help="Path to file containing the voice-design instruction",
    )
    parser.add_argument(
        "-o", "--output",
        type=str,
        default=None,
        help=f"Output audio path (default: wav file matching input name in same dir)",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=300,
        help="HTTP timeout in seconds for the TTS call (default: 300)",
    )
    args = parser.parse_args()
    # --- Read inputs ---
    text = _read_text(args.language)
    instruct = _read_instruct(args.instruct_file)

    output_path = Path(args.output) if args.output else Path(args.instruct_file).with_suffix(".wav")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Language:  {args.language} ({LANGUAGE_MAP[args.language]})")
    print(f"Text:      {len(text)} chars")
    print(f"Instruct:  {instruct[:80]}{'…' if len(instruct) > 80 else ''}")
    print(f"Output:    {output_path}")
    print()

    # --- Ensure TTS is running ---
    svc = _resolve_service_manager()
    was_running = _ensure_tts(svc)
    if was_running:
        print("TTS service was already running.")
    else:
        print("Started TTS service.")

    # --- Generate audio ---
    t0 = time.time()
    try:
        audio = _call_voice_design(
            text=text,
            language=args.language,
            instruct=instruct,
            timeout=args.timeout,
        )
        elapsed_ms = int((time.time() - t0) * 1000)
        print(f"Generated {len(audio) / 1000:.1f}s of audio in {elapsed_ms}ms.")
    except Exception:
        raise
    finally:
        # --- Stop TTS only if we started it ---
        if not was_running:
            svc.stop("tts")
            print("Stopped TTS service.")
        else:
            print("Leaving TTS service running (was active before script).")

    # --- Save output ---
    audio.export(str(output_path), format="wav")
    print(f"Saved to {output_path}")


if __name__ == "__main__":
    main()