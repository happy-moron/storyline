"""Chunk-based audio generation for the create_book pipeline.

Replaces per-sentence audio generation with:
  1. Per-chunk TTS generation (Chinese + English)
  2. Forced alignment for word-level timestamps
  3. Line-boundary timestamp calculation
  4. Aggregate audiobook assembly using timestamps
"""

import json
import logging
import os
import tomllib
from pathlib import Path

from pydub import AudioSegment

from .audiobook_gen_base import change_tempo, load_profile_from_toml
from .audiobook_gen_qwen3 import generate_tts_audio, Qwen3TTSService
from storyline.book.parse_pipe_format import parse_source_file
from storyline.book.parse_chunk_format import load_chunks_json

_log = logging.getLogger(__name__)

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_audio_toml() -> dict:
    with (CONFIG_DIR / "audio.toml").open("rb") as f:
        return tomllib.load(f)


def _get_chunk_voice(language: str) -> dict:
    """Return the voice dict for chunk-level TTS from the default profile."""
    audio_toml = _load_audio_toml()
    voices = audio_toml["voices"]
    default_seq = audio_toml["profiles"]["default"]["sequence"]
    for step in default_seq:
        if step["lang"] == language:
            voice_name = step["voice"]
            voice = dict(voices[voice_name])
            voice["voice_name"] = voice_name
            return voice
    raise KeyError(f"No voice for language '{language}' in default profile")


def _word_count(text: str, language: str) -> int:
    if language == "zh":
        return len("".join(text.split()))
    else:
        return len(text.split())


# ---------------------------------------------------------------------------
# Line timestamp calculation
# ---------------------------------------------------------------------------


def compute_line_timestamps(
    words: list[dict],
    lines: list[str],
    audio_duration: float,
    language: str,
) -> list[dict]:
    """Calculate per-line start/end timestamps from forced-alignment word data.

    Boundary between lines: (last_word_of_line.end_time + first_word_of_next.start_time) / 2.
    First line starts at 0.0; last line ends at audio_duration.
    """
    if not lines:
        return []

    if len(lines) == 1:
        return [{"start": 0.0, "end": audio_duration, "word_count": _word_count(lines[0], language)}]

    line_word_counts = [_word_count(l, language) for l in lines]
    total = sum(line_word_counts)
    if total != len(words):
        _log.warning(
            "Word count mismatch: text=%d words, aligner=%d words. Clamping.",
            total, len(words),
        )

    results: list[dict] = []
    word_pos = 0

    for i, line in enumerate(lines):
        n = line_word_counts[i]
        remaining = len(words) - word_pos
        n = max(0, min(n, remaining))
        if n <= 0 or word_pos >= len(words):
            prev_end = results[-1]["end"] if results else 0.0
            results.append({"start": prev_end, "end": prev_end, "word_count": 0})
            continue

        first_word = words[word_pos]
        last_word = words[word_pos + n - 1]

        start_time = 0.0 if i == 0 else results[-1]["end"]

        # If no words remain after this line, treat it as the last line
        if i == len(lines) - 1 or word_pos + n >= len(words):
            end_time = audio_duration
        else:
            next_first = words[word_pos + n]
            end_time = (last_word["end_time"] + next_first["start_time"]) / 2.0

        results.append({"start": round(start_time, 3), "end": round(end_time, 3), "word_count": n})
        word_pos += n

    return results


# ---------------------------------------------------------------------------
# Chunk-level TTS + alignment
# ---------------------------------------------------------------------------


def _generate_chunk_tts(
    service: Qwen3TTSService,
    text: str,
    voice: dict,
    instruct: str,
) -> AudioSegment:
    """Generate TTS audio for a chunk, passing the chunk instruct."""
    mode = voice["mode"]
    language = voice["language"]

    if mode == "custom_voice":
        return service.generate_audio(
            text, language,
            speaker=voice["speaker"],
            instruct=instruct,
        )
    elif mode == "voice_clone":
        return service.generate_voice_clone(
            text, language,
            ref_audio_path=voice["ref_audio"],
            ref_text=voice["ref_text"],
        )
    else:
        raise ValueError(f"Unknown voice mode: {mode}")


def _align_chunk_audio(
    service: Qwen3TTSService,
    audio: AudioSegment,
    text: str,
    language: str,
) -> tuple[list[dict], float]:
    """Run forced alignment and return (word_list, audio_duration_seconds)."""
    audio_duration = len(audio) / 1000.0
    words = service.forced_align(audio, text, language)
    return words, audio_duration


# ---------------------------------------------------------------------------
# Chapter-level orchestrator
# ---------------------------------------------------------------------------


def process_chapter_chunks(
    source_txt_path: str | Path,
    english_text_path: str | Path,
    chunk_json_path: str | Path,
    chapters_audio_dir: str | Path,
    aggregate_output_path: str | Path,
    profile_key: str = "default",
    service: Qwen3TTSService | None = None,
    bitrate: str = "64k",
) -> None:
    """Generate chunk-based audio for one chapter.

    1. Load chunk definitions and text
    2. For each chunk: generate zh + en TTS, run forced alignment,
       compute line timestamps, save audio files
    3. Update chunk metadata JSON
    4. Build aggregate audiobook from timestamped segments
    """
    if service is None:
        service = Qwen3TTSService()

    source_txt_path = Path(source_txt_path)
    english_text_path = Path(english_text_path)
    chunk_json_path = Path(chunk_json_path)
    chapters_audio_dir = Path(chapters_audio_dir)
    aggregate_output_path = Path(aggregate_output_path)

    os.makedirs(chapters_audio_dir, exist_ok=True)

    # -- Load data --
    chunk_defs = load_chunks_json(chunk_json_path)
    pipe_sentences = parse_source_file(source_txt_path)
    english_lines = [
        l.strip() for l in english_text_path.read_text(encoding="utf-8").strip().split("\n")
        if l.strip()
    ]

    zh_voice = _get_chunk_voice("zh")
    en_voice = _get_chunk_voice("en")

    chapter_stem = source_txt_path.stem

    # -- Process each chunk --
    for ci, chunk in enumerate(chunk_defs):
        line_range = chunk["line_range"]
        start, end = line_range[0], line_range[1]
        instruct = chunk.get("instruct", "")

        zh_lines = [pipe_sentences[i]["chinese"] for i in range(start, end + 1)]
        en_lines = [english_lines[i] for i in range(start, end + 1)]

        # Build concatenated texts for TTS
        zh_text = "".join(zh_lines)  # Chinese: no spaces
        en_text = " ".join(en_lines)  # English: join with spaces

        _log.info("  Chunk %d/%d: lines %d-%d, instruct=%r", ci + 1, len(chunk_defs), start, end, instruct or "(none)")

        # -- Chinese TTS + alignment --
        zh_audio_path = chapters_audio_dir / f"{chapter_stem}_zh_{ci:02d}.mp3"
        if not zh_audio_path.exists():
            zh_audio = _generate_chunk_tts(service, zh_text, zh_voice, instruct)
            zh_words, zh_duration = _align_chunk_audio(service, zh_audio, zh_text, zh_voice["language"])
            zh_audio.export(zh_audio_path, format="mp3", bitrate=bitrate)
        else:
            _log.info("    zh audio exists, re-loading for alignment")
            zh_audio = AudioSegment.from_file(zh_audio_path)
            zh_duration = len(zh_audio) / 1000.0
            zh_words = service.forced_align(zh_audio, zh_text, zh_voice["language"])

        zh_line_ts = compute_line_timestamps(zh_words, zh_lines, zh_duration, "zh")

        # -- English TTS + alignment --
        en_audio_path = chapters_audio_dir / f"{chapter_stem}_en_{ci:02d}.mp3"
        if not en_audio_path.exists():
            en_audio = _generate_chunk_tts(service, en_text, en_voice, "")
            en_words, en_duration = _align_chunk_audio(service, en_audio, en_text, en_voice["language"])
            en_audio.export(en_audio_path, format="mp3", bitrate=bitrate)
        else:
            _log.info("    en audio exists, re-loading for alignment")
            en_audio = AudioSegment.from_file(en_audio_path)
            en_duration = len(en_audio) / 1000.0
            en_words = service.forced_align(en_audio, en_text, en_voice["language"])

        en_line_ts = compute_line_timestamps(en_words, en_lines, en_duration, "en")

        # -- Update chunk metadata --
        chunk["audio_zh"] = f"{chapter_stem}_zh_{ci:02d}.mp3"
        chunk["audio_en"] = f"{chapter_stem}_en_{ci:02d}.mp3"
        chunk["lines"] = []
        for i, (zh_ts, en_ts) in enumerate(zip(zh_line_ts, en_line_ts)):
            chunk["lines"].append({
                "start_zh": zh_ts["start"],
                "end_zh": zh_ts["end"],
                "word_count_zh": zh_ts["word_count"],
                "start_en": en_ts["start"],
                "end_en": en_ts["end"],
                "word_count_en": en_ts["word_count"],
            })
        chunk["words_zh"] = zh_words
        chunk["words_en"] = en_words

    # -- Save updated chunk metadata --
    with open(chunk_json_path, "w", encoding="utf-8") as f:
        json.dump({"chunks": chunk_defs}, f, ensure_ascii=False, indent=2)
    _log.info("  -> updated chunk metadata: %s", chunk_json_path)

    # -- Build aggregate audiobook --
    if not aggregate_output_path.exists():
        _build_aggregate_audiobook(
            chunk_defs, chapters_audio_dir,
            aggregate_output_path, profile_key, bitrate,
        )
        _log.info("  -> aggregate audiobook: %s", aggregate_output_path)


# ---------------------------------------------------------------------------
# Aggregate audiobook assembly
# ---------------------------------------------------------------------------


def _build_aggregate_audiobook(
    chunk_defs: list[dict],
    chapters_audio_dir: Path,
    output_path: Path,
    profile_key: str,
    bitrate: str,
) -> None:
    """Build the repetition-pattern audiobook from chunk audio segments.

    Uses line timestamps to extract individual sentence audio slices,
    applies speed changes, and assembles in the profile's cadence pattern.
    """
    sequence_steps, pause_ms = load_profile_from_toml(profile_key)

    final = AudioSegment.empty()

    for chunk in chunk_defs:
        zh_audio_path = chapters_audio_dir / chunk["audio_zh"]
        en_audio_path = chapters_audio_dir / chunk["audio_en"]

        zh_audio = AudioSegment.from_file(zh_audio_path) if zh_audio_path.exists() else None
        en_audio = AudioSegment.from_file(en_audio_path) if en_audio_path.exists() else None

        if zh_audio is None or en_audio is None:
            _log.warning("  Skipping chunk — missing audio files")
            continue

        for li, line_ts in enumerate(chunk["lines"]):
            zh_slice = zh_audio[int(line_ts["start_zh"] * 1000):int(line_ts["end_zh"] * 1000)]
            en_slice = en_audio[int(line_ts["start_en"] * 1000):int(line_ts["end_en"] * 1000)]

            for step in sequence_steps:
                lang = step["lang"]
                speed = step["speed"]

                seg = zh_slice if lang == "zh" else en_slice
                if speed != 1.0:
                    seg = change_tempo(seg, speed)

                final += seg + AudioSegment.silent(duration=pause_ms)

    final.export(output_path, format="mp3", bitrate=bitrate)
    _log.info("  Aggregate audiobook written: %d chunks, %.1fs total",
              len(chunk_defs), len(final) / 1000.0)