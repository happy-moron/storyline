"""Batch-generate audio for all voice-design instruct files in voices/.

Reads every .txt file in the voices/ directory, uses it as the voice-design
instruct, and generates a .wav with the same stem.  Clears out any existing
.wav files before starting so the directory stays in sync with the .txt files.

Usage:
    python scripts/voice_design_batch.py
    python scripts/voice_design_batch.py --language zh
    python scripts/voice_design_batch.py --timeout 600
"""

import argparse
import base64
import io
import re
import sys
import time
from pathlib import Path

import requests
from pydub import AudioSegment

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from storyline.services.manager import ServiceManager, ServiceStatus  # noqa: E402

VOICES_DIR = PROJECT_ROOT / "voices"

TEXT_FILES = {
    "en": Path.home() / "temp" / "audio" / "clones" / "reference-30s-en.txt",
    "zh": Path.home() / "temp" / "audio" / "clones" / "reference-zh.txt",
}

LANGUAGE_MAP = {
    "en": "English",
    "zh": "Chinese",
}

TTS_BASE_URL = "http://127.0.0.1:11433"

_DIALOGUE_RE = re.compile(r'^dialogue\s*=\s*"(.*)"\s*$')
# Matches any key = "value" line (but not dialogue, which is stripped above)
_KEY_LINE_RE = re.compile(r'^(\w+)\s*=\s*"(.*)"\s*$')

_MANDARIN_KEYS_PATH = PROJECT_ROOT / "scripts" / "mandarin-keys.txt"


def _load_mandarin_keys() -> dict[str, str]:
    """Load English→Mandarin key mapping from mandarin-keys.txt."""
    mapping: dict[str, str] = {}
    if not _MANDARIN_KEYS_PATH.exists():
        return mapping
    for line in _MANDARIN_KEYS_PATH.read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",", 1)
        if len(parts) == 2:
            mapping[parts[0].strip()] = parts[1].strip()
    return mapping


def _preprocess_instruct(instruct: str, language: str) -> str:
    """Convert voice profile lines from TOML-style to human-friendly format.

    For each line matching ``key = "value"`` converts to ``key: value``.
    If *language* is ``"zh"``, English keys are swapped for their Mandarin
    equivalents (loaded from *mandarin-keys.txt*).  Lines that do not match
    the pattern (blank lines, non-key lines) are passed through unchanged.
    """
    key_map = _load_mandarin_keys() if language == "zh" else {}
    result: list[str] = []
    for line in instruct.split("\n"):
        m = _KEY_LINE_RE.match(line)
        if m:
            eng_key = m.group(1)
            value = m.group(2)
            display_key = key_map.get(eng_key, eng_key)
            result.append(f"{display_key}: {value}")
        else:
            result.append(line)
    return "\n".join(result)


def _normalize_curly_quotes(s: str) -> str:
    """Replace Unicode curly quotes with ASCII double quotes.

    Voice profiles are often written with typographic quotes (\u201c/\u201d)
    which won't match the ASCII-quote regexes used for parsing.
    """
    return s.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")


def _parse_voice_profile(path: str | Path) -> tuple[str | None, str]:
    """Parse a voice profile file.

    Returns (dialogue_value_or_None, instruct_content).
    The instruct content is the full file content minus the dialogue line,
    so the dialogue value is never sent as part of the instruct.
    """
    p = Path(path)
    content = p.read_text(encoding="utf-8").strip()
    content = _normalize_curly_quotes(content)

    dialogue = None
    filtered_lines = []
    for line in content.split('\n'):
        m = _DIALOGUE_RE.match(line)
        if m:
            dialogue = m.group(1)
        else:
            filtered_lines.append(line)

    instruct = '\n'.join(filtered_lines).strip()
    return dialogue, instruct


def _resolve_service_manager() -> ServiceManager:
    return ServiceManager()


def _ensure_tts(svc: ServiceManager) -> bool:
    was_running = svc.get_status("tts") == ServiceStatus.ONLINE
    if not was_running:
        svc.start("tts")
    return was_running


def _read_text(language: str) -> str:
    path = TEXT_FILES[language]
    if not path.exists():
        raise FileNotFoundError(
            f"Reference text file not found: {path}\n"
            f"Expected at dist/audio/clones/reference-{'30s-en' if language == 'en' else 'zh'}.txt"
        )
    return path.read_text(encoding="utf-8").strip()


def _call_voice_design(
    text: str, language: str, instruct: str, timeout: int = 300
) -> AudioSegment:
    payload = {
        "text": text,
        "language": LANGUAGE_MAP[language],
        "instruct": instruct,
    }
    response = requests.post(
        f"{TTS_BASE_URL}/voice_design",
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
        description="Batch-generate audio for all voice-design instruct files in voices/",
    )
    parser.add_argument(
        "-l", "--language",
        choices=sorted(TEXT_FILES.keys()),
        default="en",
        help="Language for all voice generations (default: en). "
             "Selects the corresponding reference text file.",
    )
    parser.add_argument(
        "-t", "--timeout",
        type=int,
        default=300,
        help="HTTP timeout in seconds per TTS call (default: 300)",
    )
    parser.add_argument(
        "-n", "--dry-run",
        action="store_true",
        help="Print what would be done without doing it",
    )
    args = parser.parse_args()

    voices_dir = VOICES_DIR
    if not voices_dir.exists():
        print(f"Voices directory not found: {voices_dir}")
        sys.exit(1)

    txt_files = sorted(voices_dir.glob("*.txt"))
    if not txt_files:
        print(f"No .txt files found in {voices_dir}")
        return

    wav_files = list(voices_dir.glob("*.wav"))

    print(f"Voices directory: {voices_dir}")
    print(f"Language:         {args.language} ({LANGUAGE_MAP[args.language]})")
    print(f"Text files:      {len(txt_files)}")
    print(f"Existing wavs:   {len(wav_files)}")
    print()

    if args.dry_run:
        print("Dry-run mode — no files will be changed.")
        for w in wav_files:
            print(f"  [remove] {w.name}")
        for txt in txt_files:
            wav = txt.with_suffix(".wav")
            print(f"  [generate] {txt.name} → {wav.name}")
        return

    # --- Step 1: Clear existing .wav files ---
    for w in wav_files:
        w.unlink()
        print(f"Removed {w.name}")
    print()

    # --- Step 2: Read reference text ---
    try:
        text = _read_text(args.language)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    print(f"Reference text:  {len(text)} chars")
    print()

    # --- Step 3: Ensure TTS is running ---
    svc = _resolve_service_manager()
    was_running = _ensure_tts(svc)
    if was_running:
        print("TTS service was already running.")
    else:
        print("Started TTS service.")
    print()

    # --- Step 4: Generate audio for each instruct file ---
    successes = []
    failures = []
    try:
        for txt in txt_files:
            # Parse the voice profile — dialogue becomes the reference text
            # when present, otherwise we use the language-level reference.
            dialogue, instruct = _parse_voice_profile(txt)

            # Pre-process instruct: convert TOML keys to human-friendly format
            # and swap to Mandarin keys for zh profiles.
            instruct = _preprocess_instruct(instruct, args.language)

            if dialogue:
                current_text = dialogue
                dialogue_label = " (dialogue)"
            else:
                current_text = text
                dialogue_label = ""

            wav_path = txt.with_suffix(".wav")

            print(f"[{txt.stem}] Generating{dialogue_label}…  ({len(instruct)} chars instruct)", end="")

            t0 = time.time()
            try:
                audio = _call_voice_design(
                    text=current_text,
                    language=args.language,
                    instruct=instruct,
                    timeout=args.timeout,
                )
                elapsed_ms = int((time.time() - t0) * 1000)
                audio.export(str(wav_path), format="wav")
                print(f"  {len(audio) / 1000:.1f}s audio in {elapsed_ms}ms → {wav_path.name}")
                successes.append(txt.stem)
            except Exception as e:
                elapsed_ms = int((time.time() - t0) * 1000)
                print(f"  FAILED after {elapsed_ms}ms: {e}")
                failures.append((txt.stem, str(e)))
    finally:
        # --- Stop TTS only if we started it ---
        if not was_running:
            svc.stop("tts")
            print("Stopped TTS service.")
        else:
            print("Leaving TTS service running (was active before script).")

    # --- Summary ---
    print()
    print("=" * 60)
    print(f"Done.  {len(successes)} succeeded, {len(failures)} failed.")
    if successes:
        print(f"Succeeded: {', '.join(successes)}")
    if failures:
        for name, reason in failures:
            print(f"  FAILED: {name} — {reason}")


if __name__ == "__main__":
    main()