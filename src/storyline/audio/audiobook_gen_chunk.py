"""Chunk-based audio generation for the create_book pipeline.

Replaces per-sentence audio generation with:
  1. Per-chunk TTS generation (Chinese + English)
  2. Forced alignment for word-level timestamps
  3. Line-boundary timestamp calculation
  4. Aggregate audiobook assembly using timestamps
"""

import json
import os
import time
import tomllib
from pathlib import Path

from pydub import AudioSegment

from storyline.logging import get_logger
from .audiobook_gen_base import change_tempo, load_profile_from_toml, build_id3_tags
from .audiobook_gen_qwen3 import generate_tts_audio, Qwen3TTSService
from storyline.book.parse_pipe_format import parse_source_file
from storyline.book.parse_chunk_format import load_chunks_json
_log = get_logger("audio")

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load_audio_toml() -> dict:
    with (CONFIG_DIR / "audio.toml").open("rb") as f:
        return tomllib.load(f)


def _get_chunk_voice(language: str, profile_key: str = "default") -> dict:
    """Return the voice dict for chunk-level TTS from the given profile."""
    audio_toml = _load_audio_toml()
    voices = audio_toml["voices"]
    seq = audio_toml["profiles"][profile_key]["sequence"]
    for step in seq:
        if step["lang"] == language:
            voice_name = step["voice"]
            voice = dict(voices[voice_name])
            voice["voice_name"] = voice_name
            return voice
    raise KeyError(f"No voice for language '{language}' in profile '{profile_key}'")


# ---------------------------------------------------------------------------
# Line timestamp calculation
# ---------------------------------------------------------------------------


def _build_clean_text_zh(text: str) -> str:
    """Normalize Chinese text for character-level matching.

    Removes whitespace and CJK punctuation so that aligner word characters
    can be matched against cleaned source line characters one-to-one.
    """
    from storyline.book.cjk_punct import CJK_PUNCT
    clean = "".join(text.split())
    return "".join(ch for ch in clean if ch not in CJK_PUNCT)


def _compute_zh_line_timestamps(
    words: list[dict],
    lines: list[str],
    audio_duration: float,
) -> list[dict]:
    """Character-level line-boundary detection for Chinese.

    Accumulates aligner word text (stripped of whitespace + CJK_PUNCT) and
    matches against cleaned source line text.  This handles multi-byte tokens
    like "Jeeves" (6 chars but 1 aligner token) correctly.
    """
    full_text = "".join(lines)
    clean_full = _build_clean_text_zh(full_text)

    line_end_chars: list[int] = []
    pos = 0
    for line in lines:
        pos += len(_build_clean_text_zh(line))
        line_end_chars.append(pos)

    accumulated = ""
    word_idx = 0
    line_boundaries: list[tuple[int, int]] = []

    for boundary_char in line_end_chars[:-1]:
        while word_idx < len(words) and len(accumulated) < boundary_char:
            accumulated += _build_clean_text_zh(words[word_idx]["text"])
            word_idx += 1

        if word_idx == 0:
            line_boundaries.append((-1, 0))
        elif word_idx >= len(words) and len(accumulated) < boundary_char:
            line_boundaries.append((len(words) - 1, -1))
        else:
            line_boundaries.append((word_idx - 1, word_idx))

    expected_char = len(clean_full)
    if abs(len(accumulated) - expected_char) > expected_char * 0.15:
        _log.warning(
            "Alignment text mismatch: text=%d chars, aligner accum=%d chars",
            expected_char, len(accumulated),
        )

    return _build_line_results(words, lines, line_boundaries, audio_duration)


def _compute_en_line_timestamps(
    words: list[dict],
    lines: list[str],
    audio_duration: float,
) -> list[dict]:
    """Word-level line-boundary detection for English.

    Uses ``text.split()`` to count words per line (same tokenisation the
    ForcedAligner produces) and distributes aligner word tokens accordingly.
    """
    line_word_counts = [len(line.split()) for line in lines]
    total = sum(line_word_counts)

    if total != len(words):
        _log.warning(
            "Word count mismatch: text=%d words, aligner=%d words. Clamping.",
            total, len(words),
        )

    word_pos = 0
    results: list[dict] = []

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

        if i == len(lines) - 1 or word_pos + n >= len(words):
            end_time = audio_duration
        else:
            next_first = words[word_pos + n]
            end_time = (last_word["end_time"] + next_first["start_time"]) / 2.0

        results.append({"start": round(start_time, 3), "end": round(end_time, 3), "word_count": n})
        word_pos += n

    return results


def _build_line_results(
    words: list[dict],
    lines: list[str],
    line_boundaries: list[tuple[int, int]],
    audio_duration: float,
) -> list[dict]:
    """Convert (last_idx, next_idx) boundaries into per-line timestamp dicts."""
    results: list[dict] = []
    prev_end = 0.0

    for i in range(len(lines)):
        if i == 0:
            start_time = 0.0
            first_word_idx = 0
        else:
            start_time = prev_end
            _, next_idx = line_boundaries[i - 1]
            first_word_idx = len(words) if next_idx < 0 else next_idx

        if i == len(lines) - 1:
            end_time = audio_duration
            last_word_idx = len(words) - 1
        else:
            last_idx, next_idx = line_boundaries[i]
            if last_idx < 0 or next_idx < 0 or next_idx >= len(words):
                end_time = audio_duration
                last_word_idx = last_idx if last_idx >= 0 else (len(words) - 1 if words else 0)
            else:
                end_time = (words[last_idx]["end_time"] + words[next_idx]["start_time"]) / 2.0
                last_word_idx = last_idx

        if not words or first_word_idx >= len(words):
            n_words = 0
        elif i == len(lines) - 1:
            n_words = len(words) - first_word_idx
        else:
            n_words = last_word_idx - first_word_idx + 1
        n_words = max(0, n_words)

        results.append({
            "start": round(start_time, 3),
            "end": round(end_time, 3),
            "word_count": n_words,
        })
        prev_end = end_time

    return results


def compute_line_timestamps(
    words: list[dict],
    lines: list[str],
    audio_duration: float,
    language: str,
) -> list[dict]:
    """Calculate per-line start/end timestamps from forced-alignment word data.

    Uses character-level matching for Chinese (which handles multi-byte
    tokens like "Jeeves") and word-level matching for English.

    Boundary between lines: (last_word_of_line.end_time + first_word_of_next.start_time) / 2.
    First line starts at 0.0; last line ends at audio_duration.
    """
    if not lines:
        return []

    if len(lines) == 1:
        return [{"start": 0.0, "end": audio_duration, "word_count": len(words)}]

    if language == "zh":
        return _compute_zh_line_timestamps(words, lines, audio_duration)
    else:
        return _compute_en_line_timestamps(words, lines, audio_duration)


# ---------------------------------------------------------------------------
# Chunk-level TTS + alignment
# ---------------------------------------------------------------------------


def _generate_chunk_tts(
    service: Qwen3TTSService,
    text: str,
    voice: dict,
    instruct: str,
    use_instruct: bool = True,
) -> AudioSegment:
    """Generate TTS audio for a chunk.

    When *use_instruct* is False, the chunk @instruct is suppressed.
    """
    mode = voice["mode"]
    language = voice["language"]

    if mode == "custom_voice":
        return service.generate_audio(
            text, language,
            speaker=voice["speaker"],
            instruct=instruct if use_instruct else "",
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
    book: str = "",
    author: str = "",
    service: Qwen3TTSService | None = None,
    bitrate: str = "64k",
    use_instruct: bool | None = None,
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

    zh_voice = _get_chunk_voice("zh", profile_key)
    en_voice = _get_chunk_voice("en", profile_key)

    # Resolve use_instruct: explicit override > audio.toml profile > False
    if use_instruct is None:
        use_instruct = _load_audio_toml().get("profiles", {}).get(
            profile_key, {}
        ).get("use_instruct", False)

    chapter_stem = source_txt_path.stem

    # -- Process each chunk --
    for ci, chunk in enumerate(chunk_defs):
        line_range = chunk["line_range"]
        start, end = line_range[0], line_range[1]
        instruct_en = chunk.get("instruct", "")
        instruct_zh = chunk.get("instruct_zh", "")

        zh_lines = [pipe_sentences[i]["chinese"] for i in range(start, end + 1)]
        en_lines = [english_lines[i] for i in range(start, end + 1)]

        # Build concatenated texts for TTS
        zh_text = "".join(zh_lines)  # Chinese: no spaces
        en_text = " ".join(en_lines)  # English: join with spaces

        # Validate sentence ranges
        if start < 0 or end >= len(pipe_sentences):
            _log.error(
                "  Chunk %d: line range [%d, %d] out of bounds (0..%d) — skipping",
                ci, start, end, len(pipe_sentences) - 1,
            )
            continue
        if start > end:
            _log.warning("  Chunk %d: inverted range [%d, %d] — skipping", ci, start, end)
            continue

        zh_text_sample = zh_lines[0][:40] + "..." if zh_lines[0] else "(empty)"
        en_text_sample = en_lines[0][:40] + "..." if en_lines[0] else "(empty)"
        _log.info(
            "  Chunk %d/%d: lines %d-%d (%d lines), en_instr=%r, zh_instr=%r",
            ci + 1, len(chunk_defs), start, end, len(zh_lines),
            instruct_en or "(none)", instruct_zh or "(none)",
        )
        _log.info("    zh[%d]: %s", start, zh_text_sample)
        _log.info("    en[%d]: %s", start, en_text_sample)

        # -- Chinese TTS + alignment --
        zh_audio_path = chapters_audio_dir / f"{ci:03d}_{chapter_stem}_zh.mp3"
        zh_chars = len(zh_text)
        if not zh_audio_path.exists():
            t_zh_tts = time.time()
            zh_audio = _generate_chunk_tts(service, zh_text, zh_voice, instruct_zh, use_instruct)
            zh_tts_ms = int((time.time() - t_zh_tts) * 1000)
            _log.info("event=audio_chunk chapter=%s chunk=%d/%d lang=zh chars=%d duration_ms=%d",
                      chapter_stem, ci + 1, len(chunk_defs), zh_chars, zh_tts_ms)
            t_zh_align = time.time()
            zh_words, zh_duration = _align_chunk_audio(service, zh_audio, zh_text, zh_voice["language"])
            zh_align_ms = int((time.time() - t_zh_align) * 1000)
            _log.info("event=audio_align chapter=%s chunk=%d/%d lang=zh words=%d duration_ms=%d",
                      chapter_stem, ci + 1, len(chunk_defs), len(zh_words), zh_align_ms)
            zh_audio.export(zh_audio_path, format="mp3", bitrate=bitrate)
        else:
            _log.info("    zh audio exists, re-loading for alignment")
            zh_audio = AudioSegment.from_file(zh_audio_path)
            zh_duration = len(zh_audio) / 1000.0
            t_zh_align = time.time()
            zh_words = service.forced_align(zh_audio, zh_text, zh_voice["language"])
            zh_align_ms = int((time.time() - t_zh_align) * 1000)
            _log.info("event=audio_align chapter=%s chunk=%d/%d lang=zh words=%d duration_ms=%d",
                      chapter_stem, ci + 1, len(chunk_defs), len(zh_words), zh_align_ms)

        zh_line_ts = compute_line_timestamps(zh_words, zh_lines, zh_duration, "zh")

        # -- English TTS + alignment --
        en_audio_path = chapters_audio_dir / f"{ci:03d}_{chapter_stem}_en.mp3"
        en_chars = len(en_text)
        if not en_audio_path.exists():
            t_en_tts = time.time()
            en_audio = _generate_chunk_tts(service, en_text, en_voice, instruct_en, use_instruct)
            en_tts_ms = int((time.time() - t_en_tts) * 1000)
            _log.info("event=audio_chunk chapter=%s chunk=%d/%d lang=en chars=%d duration_ms=%d",
                      chapter_stem, ci + 1, len(chunk_defs), en_chars, en_tts_ms)
            t_en_align = time.time()
            en_words, en_duration = _align_chunk_audio(service, en_audio, en_text, en_voice["language"])
            en_align_ms = int((time.time() - t_en_align) * 1000)
            _log.info("event=audio_align chapter=%s chunk=%d/%d lang=en words=%d duration_ms=%d",
                      chapter_stem, ci + 1, len(chunk_defs), len(en_words), en_align_ms)
            en_audio.export(en_audio_path, format="mp3", bitrate=bitrate)
        else:
            _log.info("    en audio exists, re-loading for alignment")
            en_audio = AudioSegment.from_file(en_audio_path)
            en_duration = len(en_audio) / 1000.0
            t_en_align = time.time()
            en_words = service.forced_align(en_audio, en_text, en_voice["language"])
            en_align_ms = int((time.time() - t_en_align) * 1000)
            _log.info("event=audio_align chapter=%s chunk=%d/%d lang=en words=%d duration_ms=%d",
                      chapter_stem, ci + 1, len(chunk_defs), len(en_words), en_align_ms)

        en_line_ts = compute_line_timestamps(en_words, en_lines, en_duration, "en")

        # -- Update chunk metadata --
        chunk["audio_zh"] = f"{ci:03d}_{chapter_stem}_zh.mp3"
        chunk["audio_en"] = f"{ci:03d}_{chapter_stem}_en.mp3"
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
        t_agg = time.time()
        _build_aggregate_audiobook(
            chunk_defs, chapters_audio_dir,
            aggregate_output_path, profile_key, bitrate,
            book=book, author=author, chapter_stem=chapter_stem,
        )
        total_s = len(AudioSegment.from_file(aggregate_output_path)) / 1000.0
        agg_ms = int((time.time() - t_agg) * 1000)
        total_segments = sum(len(chunk["lines"]) for chunk in chunk_defs)
        _log.info("event=audio_aggregate chapter=%s chunks=%d segments=%d duration_ms=%d total_s=%.1f",
                  chapter_stem, len(chunk_defs), total_segments, agg_ms, total_s)
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
    book: str = "",
    author: str = "",
    chapter_stem: str = "",
) -> None:
    """Build the repetition-pattern audiobook from chunk audio segments.

    Uses line timestamps to extract individual sentence audio slices,
    applies speed changes, and assembles in the profile's cadence pattern.
    """
    sequence_steps, pause_ms, _use_instruct = load_profile_from_toml(profile_key)

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

    tags = build_id3_tags(book, author, chapter_stem)
    final.export(output_path, format="mp3", bitrate=bitrate, tags=tags)
    _log.info("  Aggregate audiobook written: %d chunks, %.1fs total",
              len(chunk_defs), len(final) / 1000.0)