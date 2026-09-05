import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union

from pydub import AudioSegment

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.logging import get_logger
from storyline.podcast.audio_gen import LANGUAGE_MAP, voice_profile_stem
from storyline.podcast.script_parser import (
    DialogueLine,
    HostLine,
    PodcastScript,
)
from storyline.services.manager import ServiceManager

_log = get_logger("podcast.mp3_gen")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_VOICES_DIR = _PROJECT_ROOT / "voices"

_HOST_SPEAKERS = ("teacher", "student")

_DIALOGUE_RE = re.compile(r'^dialogue\s*=\s*"(.+)"\s*$')

_BATCH_SIZE = 4


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


def _load_or_clone(
    reuse_path: Path,
    speaker_id: int,
    chinese: str,
    service,
    voices_dir: Path,
    slug: str,
    script: PodcastScript,
) -> AudioSegment:
    audio = _try_load_audio(reuse_path)
    if audio is not None:
        return audio

    profile = script.voice_profiles[speaker_id]
    ref_audio = str(Path(voices_dir) / f"{voice_profile_stem(slug, speaker_id)}.wav")
    return service.generate_voice_clone(
        chinese,
        LANGUAGE_MAP[profile.lang],
        str(ref_audio),
        profile.dialogue,
    )


def _chunked_voice_clone_batch(service, texts, languages, ref_audio_path, ref_text):
    if isinstance(languages, str):
        languages = [languages] * len(texts)
    if hasattr(service, 'batch_size'):
        return service.generate_voice_clone_batch(
            texts, languages,
            ref_audio_path=ref_audio_path,
            ref_text=ref_text,
        )
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


def _build_dialogue_with_transitions(
    script: PodcastScript,
    repetitions: int,
    first_label: str,
    repeat_label: str = "Second time",
    final_label: str = "Third time",
) -> list[Step]:
    steps: list[Step] = []

    if repetitions <= 0:
        return steps

    steps.append(HostStep("teacher", "en", first_label))
    for index, line in enumerate(script.dialogue):
        steps.append(CharacterStep(line.speaker_id, line.chinese, index))

    transition_labels = [repeat_label, final_label]
    for rep in range(1, repetitions):
        if rep - 1 < len(transition_labels):
            steps.append(HostStep("teacher", "en", transition_labels[rep - 1]))
        for index, line in enumerate(script.dialogue):
            steps.append(CharacterStep(line.speaker_id, line.chinese, index))

    return steps


def _build_line_by_line_with_transitions(
    script: PodcastScript,
    repetitions: int,
    first_label: str,
    repeat_label: str = "Second time",
    final_label: str = "Third time",
) -> list[Step]:
    steps: list[Step] = []

    if repetitions <= 0:
        return steps

    steps.append(HostStep("teacher", "en", first_label))
    for line in script.dialogue:
        for _ in range(repetitions):
            steps.append(HostStep("teacher", "zh", line.chinese))
            steps.append(HostStep("student", "en", line.english))

    return steps


def build_flashcard_intro_sequence(flashcard_entries: list[list[str]]) -> list[Step]:
    steps: list[Step] = []
    if not flashcard_entries:
        return steps

    steps.append(HostStep("teacher", "en", "The vocab in this lesson is："))

    for entry in flashcard_entries:
        word = entry[0]
        meaning = entry[2]
        sentence = entry[3]
        translation = entry[5]

        steps.append(HostStep("teacher", "en", f"The chinese word... {word}"))
        steps.append(HostStep("teacher", "en", f"This means... {meaning}"))
        steps.append(HostStep("teacher", "zh", f"A sample sentence is... {sentence}"))
        steps.append(HostStep("teacher", "en", f"This means... {translation}"))
        steps.append(HostStep("teacher", "zh", sentence))
        steps.append(HostStep("teacher", "en", translation))
        steps.append(HostStep("teacher", "zh", sentence))
        steps.append(HostStep("teacher", "en", translation))

    return steps


def build_podcast_sequence(
    script: PodcastScript,
    dialogue_repetitions: int = 3,
    line_by_line_repetitions: int = 3,
    flashcard_intro: list[Step] | None = None,
) -> list[Step]:
    steps: list[Step] = []

    if flashcard_intro:
        steps.extend(flashcard_intro)

    for line in script.intro:
        steps.append(HostStep(line.speaker, line.lang, line.text))

    steps.extend(
        _build_dialogue_with_transitions(
            script, dialogue_repetitions,
            first_label="Now we'll hear the dialogue three times",
        )
    )

    steps.extend(
        _build_line_by_line_with_transitions(
            script, line_by_line_repetitions,
            first_label="Now we'll hear the dialogue with translation three times",
        )
    )

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

    steps.extend(
        _build_dialogue_with_transitions(
            script, dialogue_repetitions,
            first_label="Let's hear the dialogue three more times",
        )
    )

    for line in script.outro:
        steps.append(HostStep(line.speaker, line.lang, line.text))

    return steps


# ---------------------------------------------------------------------------
# Audio rendering
# ---------------------------------------------------------------------------

_BUILTIN_HOST_SPEAKER = {"teacher": "Serena", "student": "Ryan"}
#_BUILTIN_HOST_INSTRUCT = "Speak slightly slowly and clearly."
_BUILTIN_HOST_INSTRUCT = ""


def _prefetch_host_audio(
    steps: list[Step],
    host_service,
    clone_service,
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


def render_step(
    step: Step,
    script: PodcastScript,
    slug: str,
    base_dir: Path,
    host_service,
    clone_service,
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
            audio = host_service.generate_audio(
                step.text,
                LANGUAGE_MAP[step.lang],
                speaker=speaker,
                instruct=_BUILTIN_HOST_INSTRUCT,
            )
        else:
            ref_audio, ref_text = host_reference(step.speaker, voices_dir)
            audio = clone_service.generate_voice_clone(
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
            return _load_or_clone(
                reuse_path, step.speaker_id, step.chinese,
                clone_service, voices_dir, slug, script,
            )

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
    author: str = "podcasts",
    title: str | None = None,
    dialogue_repetitions: int = 3,
    line_by_line_repetitions: int = 3,
    pause_ms: int = 400,
    bitrate: str = "64k",
    use_builtin_hosts: bool = False,
    flashcard_intro: list[Step] | None = None,
    vocab_output_path: Path | None = None,
) -> Path:
    if voices_dir is None:
        voices_dir = _DEFAULT_VOICES_DIR
    if host_service is None:
        host_service = Qwen3TTSService()

    steps = build_podcast_sequence(
        script,
        dialogue_repetitions=dialogue_repetitions,
        line_by_line_repetitions=line_by_line_repetitions,
        flashcard_intro=flashcard_intro,
    )

    host_cache = _prefetch_host_audio(
        steps, host_service, clone_service, voices_dir, use_builtin_hosts,
    )

    # Stop TTS before any Omnivoice calls to free GPU memory.
    # When Omnivoice handles everything (use_builtin_hosts=False), TTS was
    # already stopped in create_mp3; this is a safety no-op.
    # When hosts use Qwen3TTS (use_builtin_hosts=True), we need to stop TTS
    # now so Omnivoice can load its model.
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
        combined += render_step(
            step, script, slug, base_dir, host_service, clone_service, voices_dir,
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
    use_builtin_hosts: bool = False,
    pause_ms: int = 400,
    bitrate: str = "64k",
    title: str | None = None,
    author: str = "podcasts",
) -> Path:
    host_cache = _prefetch_host_audio(
        flashcard_intro, host_service, clone_service, voices_dir, use_builtin_hosts,
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
