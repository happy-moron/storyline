import os
import subprocess
import tempfile
import tomllib
from pathlib import Path

from pydub import AudioSegment

from storyline.book.parse_pipe_format import parse_source_file


def build_id3_tags(book: str = "", author: str = "", chapter_stem: str = "") -> dict:
    tags = {}
    chapter_num = ""
    if chapter_stem and "_" in chapter_stem:
        parts = chapter_stem.rsplit("_", 1)
        if parts[-1].isdigit():
            chapter_num = parts[-1]

    title = book if book else ""
    if chapter_num:
        title = f"{book} — Chapter {int(chapter_num)}" if book else f"Chapter {int(chapter_num)}"
    if title:
        tags["title"] = title
    if chapter_num:
        tags["track"] = str(int(chapter_num))
    if book:
        tags["album"] = book
    if author:
        tags["artist"] = author
    if book or author:
        tags["genre"] = "Audiobook"

    return tags


def change_tempo(audio_segment, speed_change):
    """Change tempo without affecting pitch using soundstretch."""
    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_in:
        input_path = tmp_in.name
        audio_segment.export(input_path, format="wav")

    with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_out:
        output_path = tmp_out.name

    try:
        subprocess.run([
            "soundstretch", input_path, output_path,
            f"-tempo={int((speed_change-1)*100)}"
        ], check=True, capture_output=True)

        result = AudioSegment.from_file(output_path)
        return result
    finally:
        if os.path.exists(input_path):
            os.remove(input_path)
        if os.path.exists(output_path):
            os.remove(output_path)


def process_sentence(sentence, sequence_steps, index,
                     service, generate_tts_fn, pause_ms=300, standalone_file=None,
                     bitrate: str = "64k"):
    # Generate TTS once per unique (lang, voice) pair; speed is applied post-hoc.
    tts_cache = {}
    for step in sequence_steps:
        lang = step["lang"]
        voice_name = step.get("voice_name")
        cache_key = (lang, voice_name)
        if cache_key not in tts_cache:
            text = sentence["chinese"] if lang == "zh" else sentence["english"]
            tts_cache[cache_key] = generate_tts_fn(service, text, step["voice"])

    combined = AudioSegment.empty()

    for step in sequence_steps:
        lang = step["lang"]
        voice_name = step.get("voice_name")
        speed = step["speed"]

        seg = tts_cache[(lang, voice_name)]
        if speed != 1.0:
            seg = change_tempo(seg, speed)

        combined += seg + AudioSegment.silent(duration=pause_ms)

    if standalone_file:
        standalone_file_mp3 = standalone_file + f"_{index + 1}.mp3"
        if not os.path.exists(standalone_file_mp3):
            for step in sequence_steps:
                if step["lang"] == "zh":
                    tts_cache[(step["lang"], step.get("voice_name"))].export(
                        standalone_file_mp3, format="mp3", bitrate=bitrate)
                    break

    return combined


def process_json_to_audio_common(input_file, output_file, profile_key="default", service=None,
                                 sentence_pause=800, standalone_file=None,
                                 generate_tts_fn=None, bitrate: str = "64k",
                                 book: str = "", author: str = ""):
    sequence_steps, pause_ms, _use_instruct = load_profile_from_toml(profile_key)

    sentences = parse_source_file(input_file)

    final_audio = AudioSegment.empty()

    for i, sentence in enumerate(sentences):
        print(f"Processing sentence {i+1}/{len(sentences)}: {sentence['chinese']}")
        processed_segment = process_sentence(
            sentence, sequence_steps, i,
            service, generate_tts_fn,
            pause_ms=pause_ms, standalone_file=standalone_file,
            bitrate=bitrate,
        )
        final_audio += processed_segment
        if i < len(sentences) - 1:
            final_audio += AudioSegment.silent(duration=sentence_pause)

    chapter_stem = os.path.splitext(os.path.basename(input_file))[0]
    tags = build_id3_tags(book, author, chapter_stem)
    final_audio.export(output_file, format="mp3", bitrate=bitrate, tags=tags)
    print(f"Successfully created output file: {output_file}")


def load_profile_from_toml(profile_key="default"):
    config_path = Path(__file__).parent.parent / "config" / "audio.toml"
    with config_path.open("rb") as f:
        data = tomllib.load(f)

    profile = data.get("profiles", {}).get(profile_key, {})
    sequence = profile.get("sequence", [])
    pause_ms = profile.get("pause_ms", 300)

    use_instruct = profile.get("use_instruct", False)

    if not sequence:
        raise KeyError(f"Profile '{profile_key}' not found or has no sequence in audio.toml")

    voices = data.get("voices", {})
    sequence_steps = []

    for step in sequence:
        voice_name = step.get("voice")
        voice_data = voices.get(voice_name)
        if not voice_data:
            raise KeyError(
                f"Voice '{voice_name}' referenced in profile '{profile_key}' "
                f"not found in [voices]"
            )
        sequence_steps.append({
            "lang": step["lang"],
            "voice": voice_data,
            "voice_name": voice_name,
            "speed": step.get("speed", 1.0),
        })

    return sequence_steps, pause_ms, use_instruct
