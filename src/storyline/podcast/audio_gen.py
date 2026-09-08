import json
from pathlib import Path

from pydub import AudioSegment

from storyline.audio.audiobook_gen_qwen3 import Qwen3TTSService
from storyline.logging import get_logger
from storyline.podcast.script_parser import PodcastScript, VoiceProfile

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_DEFAULT_MANDARIN_KEYS = _PROJECT_ROOT / "scripts" / "mandarin-keys.txt"
_DEFAULT_VOICES_DIR = _PROJECT_ROOT / "voices"

_log = get_logger("podcast.audio_gen")

LANGUAGE_MAP = {"zh": "chinese", "en": "english"}

_BATCH_SIZE = 4

VOICE_DESCRIPTION_FIELDS = (
    "gender", "age", "pitch", "pace", "volume",
    "clarity", "fluency", "timbre", "emotion", "usecase",
)

SERIALIZED_FIELDS = (
    "lang", "gender", "age", "pitch", "pace", "volume",
    "clarity", "fluency", "timbre", "emotion", "usecase", "dialogue",
)


def load_mandarin_keys(path: Path = _DEFAULT_MANDARIN_KEYS) -> dict[str, str]:
    mapping: dict[str, str] = {}
    if not Path(path).exists():
        return mapping
    for line in Path(path).read_text(encoding="utf-8").strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(",", 1)
        if len(parts) == 2:
            mapping[parts[0].strip()] = parts[1].strip()
    return mapping


def voice_profile_stem(slug: str, speaker_id: int) -> str:
    return f"{slug.replace('-', '_')}_{speaker_id}"


def build_instruct(profile: VoiceProfile) -> str:
    key_map = load_mandarin_keys() if profile.lang == "zh" else {}
    lines = []
    for field in VOICE_DESCRIPTION_FIELDS:
        value = getattr(profile, field)
        display_key = key_map.get(field, field)
        lines.append(f"{display_key}: {value}")
    return "\n".join(lines)


def serialize_profile(profile: VoiceProfile) -> str:
    lines = [f"speaker = {profile.speaker}"]
    for field in SERIALIZED_FIELDS:
        lines.append(f'{field} = "{getattr(profile, field)}"')
    return "\n".join(lines) + "\n"


def generate_voice_design(service, profile: VoiceProfile, slug: str,
                          voices_dir: Path) -> tuple[Path, Path]:
    voices_dir = Path(voices_dir)
    voices_dir.mkdir(parents=True, exist_ok=True)
    stem = voice_profile_stem(slug, profile.speaker)
    txt_path = voices_dir / f"{stem}.txt"
    wav_path = voices_dir / f"{stem}.wav"
    ref_path = voices_dir / f"{stem}-ref.txt"

    txt_path.write_text(serialize_profile(profile), encoding="utf-8")

    # Write just the reference transcript for standardized clone usage
    ref_path.write_text(profile.dialogue.strip() + "\n", encoding="utf-8")

    if not wav_path.exists():
        audio = service.voice_design(
            profile.dialogue,
            LANGUAGE_MAP[profile.lang],
            build_instruct(profile),
        )
        audio.export(str(wav_path), format="wav")

    return txt_path, wav_path


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


def generate_dialogue_audio(
    script: PodcastScript,
    slug: str,
    base_dir: Path,
    service: Qwen3TTSService | None = None,
    *,
    clone_service=None,
    service_manager=None,
    voices_dir: Path | None = None,
    bitrate: str = "64k",
) -> int:
    if service is None:
        service = Qwen3TTSService()
    if clone_service is None:
        clone_service = service
    if voices_dir is None:
        voices_dir = _DEFAULT_VOICES_DIR

    base_dir = Path(base_dir)
    audio_dir = base_dir / "audio"
    chunks_dir = base_dir / "chunks"
    stem = f"{slug}_1"
    chunk_json_path = chunks_dir / f"{stem}.json"

    audio_dir.mkdir(parents=True, exist_ok=True)

    profiles = script.voice_profiles
    for profile in profiles.values():
        generate_voice_design(service, profile, slug, voices_dir)

    with open(chunk_json_path, encoding="utf-8") as f:
        chunk_data = json.load(f)
    chunks = chunk_data["chunks"]

    chunk_items: list[tuple[int, dict, int, str]] = []
    for ci, chunk in enumerate(chunks):
        line_idx = chunk["line_range"][0]
        line = script.dialogue[line_idx]
        audio_file = f"{ci:03d}_{stem}_zh.mp3"
        audio_path = audio_dir / audio_file
        chunk_items.append((ci, chunk, line.speaker_id, audio_file))

    if service_manager and clone_service is not service:
        service_manager.stop_if_running("tts")
        if hasattr(service_manager, '_wait_for_gpu_memory'):
            if not service_manager._wait_for_gpu_memory(timeout=30):
                _log.warning("event=gpu_memory_wait_timeout continuing anyway")

    for speaker_id in set(sid for _, _, sid, _ in chunk_items):
        profile = profiles[speaker_id]
        ref_audio = str(voices_dir / f"{voice_profile_stem(slug, speaker_id)}.wav")
        language = LANGUAGE_MAP[profile.lang]

        speaker_items = [(ci, chunk, af) for ci, chunk, sid, af in chunk_items
                         if sid == speaker_id]

        new_items = [(ci, chunk, af) for ci, chunk, af in speaker_items
                     if not (audio_dir / af).exists()]

        if new_items:
            texts = [script.dialogue[chunk["line_range"][0]].chinese
                     for _, chunk, _ in new_items]
            audios = _chunked_voice_clone_batch(
                clone_service, texts, language,
                ref_audio_path=ref_audio,
                ref_text=profile.dialogue,
            )
            for (ci, chunk, audio_file), audio in zip(new_items, audios):
                audio_path = audio_dir / audio_file
                audio.export(str(audio_path), format="mp3", bitrate=bitrate)
                chunk["audio_zh"] = audio_file
                chunk["lines"] = [{
                    "start_zh": 0.0,
                    "end_zh": round(len(audio) / 1000.0, 3),
                }]

        for ci, chunk, audio_file in speaker_items:
            if not chunk.get("audio_zh"):
                audio_path = audio_dir / audio_file
                audio = AudioSegment.from_file(audio_path)
                chunk["audio_zh"] = audio_file
                chunk["lines"] = [{
                    "start_zh": 0.0,
                    "end_zh": round(len(audio) / 1000.0, 3),
                }]

    with open(chunk_json_path, "w", encoding="utf-8") as f:
        json.dump(chunk_data, f, ensure_ascii=False, indent=2)

    return len(chunks)
