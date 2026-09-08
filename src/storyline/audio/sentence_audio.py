"""Normalized sentence-format audio generation.

Parses a language-tagged line-by-line format and generates TTS audio using the
specified backend engine. Owns the ServiceManager lifecycle for the audio engine.

Format:
    zh=1
    instruct=Speak warmly and slowly
    你好，今天天气真好。

    en=1
    Hello, the weather is really nice today.

    zh=2
    是啊！我特别喜欢这种天气。

Rules:
  - Each block: ``lang=speaker_id`` header, optional ``instruct=...``, then text
  - Blocks are separated by one or more blank lines
  - ``lang`` is ``zh`` or ``en``; ``speaker_id`` is a non-negative integer
  - The text line contains the sentence to speak (single line)
  - ``instruct`` is optional and provides a voice direction hint (builtin only)
"""

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydub import AudioSegment

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.logging import get_logger
from storyline.podcast.omnivoice_audio import OmnivoiceTTSService

_log = get_logger("audio.sentence")

LANGUAGE_MAP = {"zh": "chinese", "en": "english"}

_QWEN3_BATCH_SIZE = 8
_OMNIVOICE_BATCH_SIZE = 6


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SentenceBlock:
    lang: str          # "zh" or "en"
    speaker_id: int
    instruct: str      # voice direction hint, "" if none
    text: str          # text to speak
    line_number: int   # 1-based line in source

    @property
    def language(self) -> str:
        return LANGUAGE_MAP[self.lang]


@dataclass
class VoiceSpec:
    """Resolved voice specification for a speaker_id.

    ``mode`` determines which TTS endpoint to use.  Other fields are
    mode-specific and unused fields may be left empty.

    Builtin (Qwen3 /custom_voice):
        speaker — Qwen3 pretrained speaker name, e.g. "Serena", "Ryan"

    Clone (/voice_clone or Omnivoice):
        ref_audio — path to reference audio file
        ref_text  — transcript of the reference audio
    """
    mode: str = "builtin"      # "builtin" | "clone"
    speaker: str = ""          # builtin: Qwen3 speaker name
    ref_audio: str = ""        # clone: reference audio path
    ref_text: str = ""         # clone: reference transcript


# ---------------------------------------------------------------------------
# Parse error
# ---------------------------------------------------------------------------


class SentenceFormatError(Exception):
    def __init__(self, message: str, line_number: int = 0):
        self.line_number = line_number
        prefix = f"line {line_number}: " if line_number else ""
        super().__init__(f"{prefix}{message}")


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

_LANG_TAG_RE = re.compile(r'^(zh|en)=(\d+)$')
_INSTRUCT_RE = re.compile(r'^instruct=(.*)$', re.DOTALL)


def parse_sentence_format(text: str) -> list[SentenceBlock]:
    """Parse normalized sentence format into an ordered list of SentenceBlock.

    Raises SentenceFormatError on malformed input.
    """
    raw_lines = text.split("\n")
    blocks: list[SentenceBlock] = []
    i = 0

    while i < len(raw_lines):
        line_no = i + 1
        line = raw_lines[i].rstrip()

        if not line:
            i += 1
            continue

        m = _LANG_TAG_RE.match(line)
        if not m:
            raise SentenceFormatError(
                f"Expected 'zh|en=<id>', got: '{line[:60]}'",
                line_number=line_no,
            )

        lang = m.group(1)
        speaker_id = int(m.group(2))
        i += 1

        instruct = ""
        if i < len(raw_lines):
            im = _INSTRUCT_RE.match(raw_lines[i].rstrip())
            if im:
                instruct = im.group(1).strip()
                i += 1

        if i >= len(raw_lines):
            raise SentenceFormatError(
                f"Missing text line after header '{lang}={speaker_id}'",
                line_number=line_no,
            )

        text = raw_lines[i].rstrip()
        text_line_no = i + 1
        i += 1

        if not text:
            raise SentenceFormatError(
                f"Empty text after header '{lang}={speaker_id}'",
                line_number=text_line_no,
            )

        if _LANG_TAG_RE.match(text):
            raise SentenceFormatError(
                f"Expected text, got another header: '{text[:60]}'",
                line_number=text_line_no,
            )

        if _INSTRUCT_RE.match(text):
            raise SentenceFormatError(
                "instruct= must appear before the text line",
                line_number=text_line_no,
            )

        blocks.append(SentenceBlock(
            lang=lang,
            speaker_id=speaker_id,
            instruct=instruct,
            text=text,
            line_number=text_line_no,
        ))

    return blocks


# ---------------------------------------------------------------------------
# Audio generation
# ---------------------------------------------------------------------------


def _resolve_voice(
    spec: VoiceSpec | None,
    speaker_id: int,
    default_mode: str,
) -> VoiceSpec:
    if spec is not None:
        resolved = VoiceSpec(
            mode=spec.mode or default_mode,
            speaker=spec.speaker,
            ref_audio=spec.ref_audio,
            ref_text=spec.ref_text,
        )
    else:
        resolved = VoiceSpec(mode=default_mode)

    if resolved.mode == "builtin" and not resolved.speaker:
        raise SentenceFormatError(
            f"Speaker {speaker_id}: builtin mode requires a speaker name",
        )
    if resolved.mode == "clone" and (not resolved.ref_audio or not resolved.ref_text):
        raise SentenceFormatError(
            f"Speaker {speaker_id}: clone mode requires ref_audio and ref_text",
        )
    if resolved.mode not in ("builtin", "clone"):
        raise SentenceFormatError(
            f"Speaker {speaker_id}: unknown mode '{resolved.mode}'",
        )

    return resolved


def _batch_key_builtin(block: SentenceBlock, voice: VoiceSpec) -> tuple:
    return ("builtin", voice.speaker, block.instruct)


def _batch_key_clone(block: SentenceBlock, voice: VoiceSpec) -> tuple:
    return ("clone", voice.ref_audio, voice.ref_text)


def _generate_builtin_batch(
    service,
    batch: list[tuple[SentenceBlock, VoiceSpec]],
    cache: dict[tuple, AudioSegment],
) -> None:
    texts = [b.text for b, _ in batch]
    languages = [b.language for b, _ in batch]
    speakers = [v.speaker for _, v in batch]
    instructs = [b.instruct or "" for b, _ in batch]

    audios = service.generate_audio_batch(texts, languages, speakers, instructs)
    for (block, voice), audio in zip(batch, audios):
        cache[(block.lang, block.speaker_id, block.instruct, block.text)] = audio


def _generate_clone_batch(
    service,
    batch: list[tuple[SentenceBlock, VoiceSpec]],
    cache: dict[tuple, AudioSegment],
) -> None:
    texts = [b.text for b, _ in batch]
    languages = [b.language for b, _ in batch]

    voice = batch[0][1]
    audios = service.generate_voice_clone_batch(
        texts, languages,
        ref_audio_path=voice.ref_audio,
        ref_text=voice.ref_text,
    )
    for (block, _), audio in zip(batch, audios):
        cache[(block.lang, block.speaker_id, block.instruct, block.text)] = audio


def generate_sentence_audio(
    blocks: list[SentenceBlock],
    speaker_map: dict[int, VoiceSpec],
    *,
    engine: str = "qwen3",
    mode: str = "builtin",
    service_manager=None,
) -> list[tuple[SentenceBlock, AudioSegment]]:
    """Generate audio for each sentence block.

    Args:
        blocks: Parsed blocks from :func:`parse_sentence_format`.
        speaker_map: ``speaker_id`` → :class:`VoiceSpec`.  Must include an
            entry for every ``speaker_id`` referenced by *blocks*.
        engine: ``"qwen3"`` (systemd-backed) or ``"omnivoice"`` (subprocess).
        mode: ``"builtin"`` or ``"clone"`` — default when a VoiceSpec has
            an empty mode string.
        service_manager: Optional :class:`ServiceManager` for lifecycle.
            If provided and *engine* is ``"qwen3"``, the TTS service is
            started (LLM is stopped first to free GPU memory).  The service
            is left running on return.

    Returns:
        List of ``(block, audio)`` in the same order as *blocks*.
    """
    if not blocks:
        return []

    if engine == "qwen3":
        if service_manager:
            service_manager.stop_if_running("llm")
            service_manager.start_if_needed("tts")
        service = Qwen3TTSService()
        batch_size = _QWEN3_BATCH_SIZE
    elif engine == "omnivoice":
        if service_manager:
            service_manager.stop_if_running("llm")
            service_manager.stop_if_running("tts")
        service = OmnivoiceTTSService()
        batch_size = _OMNIVOICE_BATCH_SIZE
    else:
        raise ValueError(f"Unknown audio engine: {engine}")

    t0 = time.time()

    # Resolve all voices up front, raising early on missing/misconfigured
    resolved: dict[int, VoiceSpec] = {}
    for block in blocks:
        sid = block.speaker_id
        if sid not in resolved:
            resolved[sid] = _resolve_voice(speaker_map.get(sid), sid, mode)

    # Group blocks by batch key
    builtin_groups: dict[tuple, list[tuple[SentenceBlock, VoiceSpec]]] = {}
    clone_groups: dict[tuple, list[tuple[SentenceBlock, VoiceSpec]]] = {}

    for block in blocks:
        voice = resolved[block.speaker_id]
        if voice.mode == "builtin":
            key = _batch_key_builtin(block, voice)
            builtin_groups.setdefault(key, []).append((block, voice))
        else:
            key = _batch_key_clone(block, voice)
            clone_groups.setdefault(key, []).append((block, voice))

    cache: dict[tuple, AudioSegment] = {}

    # Generate builtin batches
    for key, group in builtin_groups.items():
        _log.info(
            "event=tts_builtin_group speaker=%s instruct=%s count=%d",
            key[1], key[2][:40] if key[2] else "(none)", len(group),
        )
        for i in range(0, len(group), batch_size):
            _generate_builtin_batch(service, group[i:i + batch_size], cache)

    # Generate clone batches
    for key, group in clone_groups.items():
        ref_audio = Path(key[1]).name if key[1] else "?"
        _log.info(
            "event=tts_clone_group ref=%s count=%d",
            ref_audio, len(group),
        )
        for i in range(0, len(group), batch_size):
            _generate_clone_batch(service, group[i:i + batch_size], cache)

    # Reassemble in original order
    result: list[tuple[SentenceBlock, AudioSegment]] = []
    for block in blocks:
        ck = (block.lang, block.speaker_id, block.instruct, block.text)
        result.append((block, cache[ck]))

    total_ms = int((time.time() - t0) * 1000)
    _log.info(
        "event=tts_complete blocks=%d builtin_groups=%d clone_groups=%d duration_ms=%d",
        len(blocks), len(builtin_groups), len(clone_groups), total_ms,
    )

    return result