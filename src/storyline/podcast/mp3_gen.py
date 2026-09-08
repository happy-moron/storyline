"""Podcast audio rendering — generates audio segments and assembles final MP3."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from pydub import AudioSegment

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.logging import get_logger
from storyline.podcast.audio_gen import LANGUAGE_MAP, voice_profile_stem
from storyline.podcast.script_parser import PodcastScript
from storyline.podcast.steps import (
    build_flashcard_intro_sequence,
    build_podcast_sequence,
    CharacterStep,
    HostStep,
    Step,
)
from storyline.services.manager import ServiceManager

_log = get_logger("podcast.mp3_gen")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_VOICES_DIR = _PROJECT_ROOT / "voices"

_HOST_SPEAKERS = ("teacher", "student")
_BATCH_SIZE = 4


# ---------------------------------------------------------------------------
# Host voice config
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostVoiceConfig:
    """Mapping from host role to builtin Qwen3 speaker name."""
    teacher_builtin_speaker: str = "Serena"
    student_builtin_speaker: str = "Ryan"
    builtin_instruct: str = ""


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def _try_load_audio(path: Path) -> AudioSegment | None:
    try:
        return AudioSegment.from_file(path)
    except Exception:
        _log.warning("event=bad_audio_file file=%s", path)
        try:
            path.unlink()
        except OSError:
            pass
        return None


def host_reference(speaker: str, voices_dir: Path) -> tuple[Path, str]:
    if speaker not in _HOST_SPEAKERS:
        raise ValueError(f"Unknown host speaker: {speaker}")
    voices_dir = Path(voices_dir)
    wav = voices_dir / f"{speaker}.wav"
    ref = voices_dir / f"{speaker}-ref.txt"
    return wav, ref.read_text(encoding="utf-8").strip()


def _chunked_voice_clone_batch(service, texts, languages, ref_audio_path, ref_text):
    if isinstance(languages, str):
        languages = [languages] * len(texts)
    audios = []
    for i in range(0, len(texts), _BATCH_SIZE):
        batch_texts = texts[i:i + _BATCH_SIZE]
        batch_languages = languages[i:i + _BATCH_SIZE]
        audios.extend(
            service.generate_voice_clone_batch(
                batch_texts, batch_languages,
                ref_audio_path=ref_audio_path,
                ref_text=ref_text,
            )
        )
    return audios


def _chunked_audio_batch(service, texts, languages, speakers, instructs):
    if isinstance(languages, str):
        languages = [languages] * len(texts)
    if isinstance(speakers, str):
        speakers = [speakers] * len(texts)
    if isinstance(instructs, str):
        instructs = [instructs] * len(texts)
    audios = []
    for i in range(0, len(texts), _BATCH_SIZE):
        audios.extend(
            service.generate_audio_batch(
                texts[i:i + _BATCH_SIZE],
                languages[i:i + _BATCH_SIZE],
                speakers[i:i + _BATCH_SIZE],
                instructs[i:i + _BATCH_SIZE],
            )
        )
    return audios


# ---------------------------------------------------------------------------
# Audio prefetch (batched, cached)
# ---------------------------------------------------------------------------

def _prefetch_host_audio(
    steps: list[Step],
    host_service,
    clone_service,
    voices_dir: Path,
    host_voice: HostVoiceConfig,
    use_builtin_hosts: bool,
) -> dict[tuple[str, str, str], AudioSegment]:
    cache: dict[tuple[str, str, str], AudioSegment] = {}

    if use_builtin_hosts:
        groups: dict[str, dict[str, HostStep]] = {}
        for step in steps:
            if isinstance(step, HostStep):
                builtin = (
                    host_voice.teacher_builtin_speaker
                    if step.speaker == "teacher"
                    else host_voice.student_builtin_speaker
                )
                groups.setdefault(builtin, {})[step.text] = step

        for builtin, unique in groups.items():
            steps_unique = list(unique.values())
            texts = [s.text for s in steps_unique]
            languages = [LANGUAGE_MAP[s.lang] for s in steps_unique]
            speakers = [builtin] * len(steps_unique)
            instructs = [host_voice.builtin_instruct] * len(steps_unique)
            audios = _chunked_audio_batch(host_service, texts, languages, speakers, instructs)
            for s, a in zip(steps_unique, audios):
                cache[(s.speaker, s.lang, s.text)] = a
    else:
        groups: dict[str, dict[str, HostStep]] = {}
        for step in steps:
            if isinstance(step, HostStep):
                groups.setdefault(step.speaker, {})[step.text] = step

        for speaker, unique in groups.items():
            ref_audio, ref_text = host_reference(speaker, voices_dir)
            steps_unique = list(unique.values())
            texts = [s.text for s in steps_unique]
            languages = [LANGUAGE_MAP[s.lang] for s in steps_unique]
            audios = _chunked_voice_clone_batch(
                clone_service, texts, languages, str(ref_audio), ref_text,
            )
            for s, a in zip(steps_unique, audios):
                cache[(s.speaker, s.lang, s.text)] = a

    return cache


def _prefetch_character_audio(
    steps: list[Step],
    script: PodcastScript,
    slug: str,
    base_dir: Path,
    clone_service,
    voices_dir: Path,
) -> dict[tuple[int, str], AudioSegment]:
    cache: dict[tuple[int, str], AudioSegment] = {}
    groups: dict[int, dict[str, CharacterStep]] = {}

    for step in steps:
        if not isinstance(step, CharacterStep):
            continue
        if step.dialogue_index is not None:
            reuse_path = (
                Path(base_dir) / "audio"
                / f"{step.dialogue_index:03d}_{slug}_1_zh.mp3"
            )
            if _try_load_audio(reuse_path) is not None:
                continue
        groups.setdefault(step.speaker_id, {})[step.chinese] = step

    for speaker_id, unique in groups.items():
        profile = script.voice_profiles[speaker_id]
        ref_audio = str(Path(voices_dir) / f"{voice_profile_stem(slug, speaker_id)}.wav")
        steps_unique = list(unique.values())
        texts = [s.chinese for s in steps_unique]
        audios = _chunked_voice_clone_batch(
            clone_service, texts, LANGUAGE_MAP[profile.lang],
            ref_audio_path=ref_audio, ref_text=profile.dialogue,
        )
        for s, a in zip(steps_unique, audios):
            cache[(s.speaker_id, s.chinese)] = a

    return cache


# ---------------------------------------------------------------------------
# Step rendering (single-step, used as cache fallback)
# ---------------------------------------------------------------------------

def _render_character(
    step: CharacterStep,
    script: PodcastScript,
    slug: str,
    base_dir: Path,
    clone_service,
    voices_dir: Path,
) -> AudioSegment:
    profile = script.voice_profiles[step.speaker_id]
    ref_audio = Path(voices_dir) / f"{voice_profile_stem(slug, step.speaker_id)}.wav"

    if step.dialogue_index is not None:
        reuse_path = (
            Path(base_dir)
            / "audio"
            / f"{step.dialogue_index:03d}_{slug}_1_zh.mp3"
        )
        if reuse_path.exists():
            audio = _try_load_audio(reuse_path)
            if audio is not None:
                return audio

    return clone_service.generate_voice_clone(
        step.chinese,
        LANGUAGE_MAP[profile.lang],
        str(ref_audio),
        profile.dialogue,
    )


# ---------------------------------------------------------------------------
# ID3 metadata
# ---------------------------------------------------------------------------

def build_podcast_tags(episode_name: str, author: str = "podcasts") -> dict:
    return {
        "title": episode_name,
        "artist": author,
        "album": author,
        "genre": "Podcast",
    }


# ---------------------------------------------------------------------------
# Assembly
# ---------------------------------------------------------------------------

def assemble_podcast_mp3(
    script: PodcastScript,
    slug: str,
    clone_service,
    base_dir: Path,
    output_path: Path,
    *,
    host_service=None,
    service_manager: ServiceManager | None = None,
    voices_dir: Path | None = None,
    host_voice: HostVoiceConfig | None = None,
    author: str = "podcasts",
    title: str | None = None,
    dialogue_repetitions: int = 3,
    line_by_line_repetitions: int = 3,
    pause_ms: int = 400,
    bitrate: str = "64k",
    use_builtin_hosts: bool = False,
    flashcard_intro: list[Step] | None = None,
    flashcard_audio_cache: dict[tuple[str, str, str], AudioSegment] | None = None,
    vocab_output_path: Path | None = None,
) -> Path:
    if voices_dir is None:
        voices_dir = _DEFAULT_VOICES_DIR
    if host_service is None:
        host_service = Qwen3TTSService()
    if host_voice is None:
        host_voice = HostVoiceConfig()

    steps = build_podcast_sequence(
        script,
        dialogue_repetitions=dialogue_repetitions,
        line_by_line_repetitions=line_by_line_repetitions,
        flashcard_intro=flashcard_intro,
    )

    host_cache = _prefetch_host_audio(
        steps, host_service, clone_service, voices_dir, host_voice, use_builtin_hosts,
    )

    if flashcard_audio_cache:
        host_cache.update(flashcard_audio_cache)

    if service_manager and clone_service is not host_service:
        service_manager.stop_if_running("tts")
        if hasattr(service_manager, '_wait_for_gpu_memory'):
            if not service_manager._wait_for_gpu_memory(timeout=30):
                _log.warning("event=gpu_memory_wait_timeout continuing anyway")

    character_cache = _prefetch_character_audio(
        steps, script, slug, base_dir, clone_service, voices_dir,
    )

    combined = AudioSegment.empty()
    for step in steps:
        if len(combined) > 0:
            combined += AudioSegment.silent(duration=pause_ms)

        if isinstance(step, HostStep):
            combined += host_cache[(step.speaker, step.lang, step.text)]
        elif isinstance(step, CharacterStep):
            ck = (step.speaker_id, step.chinese)
            if ck in character_cache:
                combined += character_cache[ck]
            else:
                combined += _render_character(
                    step, script, slug, base_dir, clone_service, voices_dir,
                )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(
        str(output_path),
        format="mp3",
        bitrate=bitrate,
        tags=build_podcast_tags(title or slug, author),
    )

    if vocab_output_path and flashcard_intro:
        vocab_combined = AudioSegment.empty()
        for step in flashcard_intro:
            if len(vocab_combined) > 0:
                vocab_combined += AudioSegment.silent(duration=pause_ms)
            cache_key = (step.speaker, step.lang, step.text)
            vocab_combined += host_cache[cache_key]
        vocab_output_path = Path(vocab_output_path)
        vocab_output_path.parent.mkdir(parents=True, exist_ok=True)
        vocab_combined.export(
            str(vocab_output_path),
            format="mp3",
            bitrate=bitrate,
            tags=build_podcast_tags(f"{title or slug} - Vocab", author),
        )

    return output_path


def assemble_flashcard_vocab_mp3(
    flashcard_intro: list[Step],
    output_path: Path,
    host_service,
    clone_service,
    voices_dir: Path,
    *,
    host_voice: HostVoiceConfig | None = None,
    use_builtin_hosts: bool = False,
    pause_ms: int = 400,
    bitrate: str = "64k",
    title: str | None = None,
    author: str = "podcasts",
) -> Path:
    if host_voice is None:
        host_voice = HostVoiceConfig()

    host_cache = _prefetch_host_audio(
        flashcard_intro, host_service, clone_service, voices_dir,
        host_voice, use_builtin_hosts,
    )

    combined = AudioSegment.empty()
    for step in flashcard_intro:
        if len(combined) > 0:
            combined += AudioSegment.silent(duration=pause_ms)
        cache_key = (step.speaker, step.lang, step.text)
        combined += host_cache[cache_key]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(
        str(output_path),
        format="mp3",
        bitrate=bitrate,
        tags=build_podcast_tags(title or "vocab", author),
    )
    return output_path