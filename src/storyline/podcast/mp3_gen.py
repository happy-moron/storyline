import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from pydub import AudioSegment

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.podcast.audio_gen import LANGUAGE_MAP, voice_profile_stem
from storyline.podcast.script_parser import (
    DialogueLine,
    HostLine,
    PodcastScript,
)

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_VOICES_DIR = _PROJECT_ROOT / "voices"

_HOST_SPEAKERS = ("teacher", "student")

_DIALOGUE_RE = re.compile(r'^dialogue\s*=\s*"(.+)"\s*$')

_BATCH_SIZE = 4


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
# Audio step model
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class HostStep:
    speaker: str
    lang: str
    text: str


@dataclass(frozen=True)
class CharacterStep:
    speaker_id: int
    chinese: str
    dialogue_index: int | None = None


Step = Union[HostStep, CharacterStep]


# ---------------------------------------------------------------------------
# Voice profile helpers
# ---------------------------------------------------------------------------

def normalize_curly_quotes(text: str) -> str:
    return (
        text.replace("\u201c", '"')
        .replace("\u201d", '"')
        .replace("\u2018", "'")
        .replace("\u2019", "'")
    )


def read_profile_dialogue(path: Path) -> str:
    content = normalize_curly_quotes(Path(path).read_text(encoding="utf-8"))
    for line in content.splitlines():
        match = _DIALOGUE_RE.match(line.strip())
        if match:
            return match.group(1)
    raise ValueError(f"No dialogue field found in voice profile: {path}")


def host_reference(speaker: str, voices_dir: Path) -> tuple[Path, str]:
    if speaker not in _HOST_SPEAKERS:
        raise ValueError(f"Unknown host speaker: {speaker}")
    voices_dir = Path(voices_dir)
    wav = voices_dir / f"{speaker}.wav"
    txt = voices_dir / f"{speaker}.txt"
    return wav, read_profile_dialogue(txt)


# ---------------------------------------------------------------------------
# Sequence planning
# ---------------------------------------------------------------------------

def dialogue_index_map(script: PodcastScript) -> dict[str, int]:
    result: dict[str, int] = {}
    for index, line in enumerate(script.dialogue):
        if line.chinese not in result:
            result[line.chinese] = index
    return result


def build_podcast_sequence(
    script: PodcastScript,
    dialogue_repetitions: int = 3,
    line_by_line_repetitions: int = 3,
) -> list[Step]:
    steps: list[Step] = []

    for line in script.intro:
        steps.append(HostStep(line.speaker, line.lang, line.text))

    for _ in range(dialogue_repetitions):
        for index, line in enumerate(script.dialogue):
            steps.append(CharacterStep(line.speaker_id, line.chinese, index))

    for line in script.dialogue:
        for _ in range(line_by_line_repetitions):
            steps.append(HostStep("teacher", "zh", line.chinese))
            steps.append(HostStep("student", "en", line.english))

    index_by_chinese = dialogue_index_map(script)
    for item in script.breakdown:
        if isinstance(item, HostLine):
            steps.append(HostStep(item.speaker, item.lang, item.text))
        else:
            steps.append(
                CharacterStep(
                    item.speaker_id,
                    item.chinese,
                    index_by_chinese.get(item.chinese),
                )
            )

    for line in script.outro:
        steps.append(HostStep(line.speaker, line.lang, line.text))

    return steps


# ---------------------------------------------------------------------------
# Audio rendering
# ---------------------------------------------------------------------------

_BUILTIN_HOST_SPEAKER = {"teacher": "Serena", "student": "Eric"}
_BUILTIN_HOST_INSTRUCT = "Speak slightly slowly and clearly."


def _prefetch_host_audio(
    steps: list[Step],
    service: Qwen3TTSService,
    voices_dir: Path,
    use_builtin_hosts: bool,
) -> dict[tuple[str, str, str], AudioSegment]:
    cache: dict[tuple[str, str, str], AudioSegment] = {}

    if use_builtin_hosts:
        groups: dict[str, dict[str, HostStep]] = {}
        for step in steps:
            if isinstance(step, HostStep):
                builtin = _BUILTIN_HOST_SPEAKER[step.speaker]
                groups.setdefault(builtin, {})[step.text] = step

        for builtin, unique in groups.items():
            steps_unique = list(unique.values())
            texts = [s.text for s in steps_unique]
            languages = [LANGUAGE_MAP[s.lang] for s in steps_unique]
            speakers = [builtin] * len(steps_unique)
            instructs = [_BUILTIN_HOST_INSTRUCT] * len(steps_unique)
            audios = _chunked_audio_batch(service, texts, languages, speakers, instructs)
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
                service, texts, languages, str(ref_audio), ref_text,
            )
            for s, a in zip(steps_unique, audios):
                cache[(s.speaker, s.lang, s.text)] = a

    return cache


def _prefetch_character_audio(
    steps: list[Step],
    script: PodcastScript,
    slug: str,
    base_dir: Path,
    service: Qwen3TTSService,
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
            if reuse_path.exists():
                continue
        groups.setdefault(step.speaker_id, {})[step.chinese] = step

    for speaker_id, unique in groups.items():
        profile = script.voice_profiles[speaker_id]
        ref_audio = str(Path(voices_dir) / f"{voice_profile_stem(slug, speaker_id)}.wav")
        steps_unique = list(unique.values())
        texts = [s.chinese for s in steps_unique]
        audios = _chunked_voice_clone_batch(
            service, texts, LANGUAGE_MAP[profile.lang],
            ref_audio_path=ref_audio, ref_text=profile.dialogue,
        )
        for s, a in zip(steps_unique, audios):
            cache[(s.speaker_id, s.chinese)] = a

    return cache


def render_step(
    step: Step,
    script: PodcastScript,
    slug: str,
    base_dir: Path,
    service: Qwen3TTSService,
    voices_dir: Path,
    host_cache: Optional[dict[tuple[str, str, str], AudioSegment]] = None,
    character_cache: Optional[dict[tuple[int, str], AudioSegment]] = None,
    use_builtin_hosts: bool = False,
) -> AudioSegment:
    if isinstance(step, HostStep):
        cache_key = (step.speaker, step.lang, step.text)
        if host_cache is not None and cache_key in host_cache:
            return host_cache[cache_key]
        if use_builtin_hosts:
            speaker = _BUILTIN_HOST_SPEAKER[step.speaker]
            audio = service.generate_audio(
                step.text,
                LANGUAGE_MAP[step.lang],
                speaker=speaker,
                instruct=_BUILTIN_HOST_INSTRUCT,
            )
        else:
            ref_audio, ref_text = host_reference(step.speaker, voices_dir)
            audio = service.generate_voice_clone(
                step.text,
                LANGUAGE_MAP[step.lang],
                str(ref_audio),
                ref_text,
            )
        if host_cache is not None:
            host_cache[cache_key] = audio
        return audio

    ck = (step.speaker_id, step.chinese)
    if character_cache is not None and ck in character_cache:
        return character_cache[ck]

    profile = script.voice_profiles[step.speaker_id]
    ref_audio = Path(voices_dir) / f"{voice_profile_stem(slug, step.speaker_id)}.wav"

    if step.dialogue_index is not None:
        reuse_path = (
            Path(base_dir)
            / "audio"
            / f"{step.dialogue_index:03d}_{slug}_1_zh.mp3"
        )
        if reuse_path.exists():
            return AudioSegment.from_file(reuse_path)

    return service.generate_voice_clone(
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
    service: Qwen3TTSService,
    base_dir: Path,
    output_path: Path,
    *,
    voices_dir: Path | None = None,
    author: str = "podcasts",
    title: str | None = None,
    dialogue_repetitions: int = 3,
    line_by_line_repetitions: int = 3,
    pause_ms: int = 400,
    bitrate: str = "64k",
    use_builtin_hosts: bool = False,
) -> Path:
    if voices_dir is None:
        voices_dir = _DEFAULT_VOICES_DIR

    steps = build_podcast_sequence(
        script,
        dialogue_repetitions=dialogue_repetitions,
        line_by_line_repetitions=line_by_line_repetitions,
    )

    host_cache = _prefetch_host_audio(steps, service, voices_dir, use_builtin_hosts)
    character_cache = _prefetch_character_audio(
        steps, script, slug, base_dir, service, voices_dir,
    )

    combined = AudioSegment.empty()
    for step in steps:
        if len(combined) > 0:
            combined += AudioSegment.silent(duration=pause_ms)
        combined += render_step(
            step, script, slug, base_dir, service, voices_dir,
            host_cache=host_cache,
            character_cache=character_cache,
            use_builtin_hosts=use_builtin_hosts,
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(
        str(output_path),
        format="mp3",
        bitrate=bitrate,
        tags=build_podcast_tags(title or slug, author),
    )
    return output_path
