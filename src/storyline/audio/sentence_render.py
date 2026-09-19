"""Standalone sentence rendering — TTS audio from a sentence-format script.

Parses a sentence-format script (``zh=1`` / ``en=2`` / ``instruct=...`` / text),
generates per-line WAV files and a joined MP3 using the specified TTS engine.

Usage::

    python -m storyline.audio.sentence_render script.txt
    python -m storyline.audio.sentence_render script.txt --config my_render.toml
    python -m storyline.audio.sentence_render script.txt -o output_dir --engine omnivoice

All config values can be overridden on the command line.
"""

import argparse
import tomllib
from pathlib import Path
from typing import Optional

from pydub import AudioSegment

from storyline.audio.sentence_audio import (
    VoiceSpec,
    generate_sentence_audio,
    parse_sentence_format,
    SentenceBlock,
    SentenceFormatError,
)
from storyline.logging import get_logger, init as init_logging
from storyline.services.manager import ServiceManager

_log = get_logger("audio.render")

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "render.toml"


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------


def _load_config(path: Path | str | None = None) -> dict:
    """Load render TOML config, falling back to the default."""
    if path is None:
        path = _DEFAULT_CONFIG_PATH
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Render config not found: {path}")
    with path.open("rb") as f:
        cfg = tomllib.load(f)
    return cfg


def _resolve_speaker_map(voices_cfg: dict, global_mode: str) -> dict[int, VoiceSpec]:
    """Build the speaker_id → VoiceSpec map from config voice sections."""
    speaker_map: dict[int, VoiceSpec] = {}
    for key, v in voices_cfg.items():
        try:
            sid = int(key)
        except ValueError:
            raise ValueError(
                f"Voice key must be a numeric speaker_id, got: {key!r}"
            ) from None

        mode = v.get("mode", global_mode)

        if mode == "builtin":
            speaker = v.get("speaker", "")
            if not speaker:
                raise ValueError(
                    f"Voice {sid}: builtin mode requires a 'speaker' field"
                )
            speaker_map[sid] = VoiceSpec(
                mode="builtin",
                speaker=speaker,
                ref_audio="",
                ref_text="",
            )

        elif mode == "clone":
            ref_audio = v.get("ref_audio", "")
            ref_text = v.get("ref_text", "")
            if not ref_audio or not ref_text:
                raise ValueError(
                    f"Voice {sid}: clone mode requires 'ref_audio' and 'ref_text'"
                )
            speaker_map[sid] = VoiceSpec(
                mode="clone",
                speaker="",
                ref_audio=ref_audio,
                ref_text=ref_text,
            )

        else:
            raise ValueError(
                f"Voice {sid}: unknown mode '{mode}' (expected 'builtin' or 'clone')"
            )

    # Validate that every speaker_id referenced in the script will be found
    # (caller should call _check_speaker_coverage separately, or rely on
    #  the validation in generate_sentence_audio)
    return speaker_map


def _check_speaker_coverage(
    blocks: list[SentenceBlock],
    speaker_map: dict[int, VoiceSpec],
):
    """Raise early if the script references a speaker_id not in the config."""
    referenced = {b.speaker_id for b in blocks}
    missing = referenced - set(speaker_map.keys())
    if missing:
        missing_ids = sorted(missing)
        raise ValueError(
            f"Script references speaker_id(s) {missing_ids} "
            f"but config only defines: {sorted(speaker_map.keys())}"
        )


# ---------------------------------------------------------------------------
# Output writing
# ---------------------------------------------------------------------------


def _sanitise(text: str, max_len: int = 20) -> str:
    """Truncate and sanitise a string for use in filenames."""
    cleaned = "".join(
        c if c.isalnum() or c in ("-", "_", " ") else "_" for c in text
    )
    return cleaned[:max_len].strip().replace(" ", "_")


def _write_per_line_audio(
    results: list[tuple[SentenceBlock, AudioSegment]],
    output_dir: Path,
    per_line_format: str,
) -> list[Path]:
    """Write each line's audio to a file using the format template.

    Returns the list of written file paths, in script order.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    for idx, (block, audio) in enumerate(results):
        line_number = idx + 1  # 1-based
        instruct_safe = _sanitise(block.instruct) if block.instruct else "no_instruct"

        filename = per_line_format.format(
            line_number=line_number,
            lang=block.lang,
            speaker_id=block.speaker_id,
            instruct=instruct_safe,
        )
        out_path = output_dir / filename
        audio.export(str(out_path), format="wav")
        written.append(out_path)

    return written


def _build_joined_mp3(
    results: list[tuple[SentenceBlock, AudioSegment]],
    output_path: Path,
    inter_line_pause_ms: int,
    bitrate: str,
) -> Path:
    """Concatenate all line audio segments into a single MP3."""
    paused = AudioSegment.silent(duration=inter_line_pause_ms)
    combined = AudioSegment.empty()

    for idx, (_, audio) in enumerate(results):
        if idx > 0:
            combined += paused
        combined += audio

    output_path.parent.mkdir(parents=True, exist_ok=True)
    combined.export(str(output_path), format="mp3", bitrate=bitrate)
    return output_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def render_sentences(
    script_path: str | Path,
    config_path: str | Path | None = None,
    *,
    service_manager: Optional[ServiceManager] = None,
    output_dir: str | Path | None = None,
    engine: str | None = None,
    mode: str | None = None,
    inter_line_pause_ms: int | None = None,
    bitrate: str | None = None,
) -> dict:
    """Render a sentence-format script to per-line audio and a joined MP3.

    Args:
        script_path: Path to a file in sentence format
            (see :func:`parse_sentence_format`).
        config_path: Path to a render TOML config.  If *None*, the default
            config at ``config/render.toml`` is used.
        service_manager: Optional :class:`ServiceManager`.  If provided,
            the LLM is stopped and the TTS engine is started automatically.
        output_dir: Override the output directory from config.
        engine: Override the TTS backend from config (``"qwen3"`` or
            ``"omnivoice"``).
        mode: Override the voice mode from config (``"builtin"`` or
            ``"clone"``).
        inter_line_pause_ms: Override the inter-line pause from config.
        bitrate: Override the MP3 bitrate from config.

    Returns:
        A dict with keys:

            ``script_path``       — resolved path to the input script
            ``config_path``       — resolved path to the config used
            ``engine``            — TTS backend used
            ``mode``              — voice mode used
            ``blocks``            — number of sentence blocks rendered
            ``per_line_files``    — list of Paths to written per-line WAVs
            ``joined_path``       — Path to the joined MP3
            ``joined_duration_s`` — duration of the joined MP3 in seconds
    """
    import time

    t_start = time.time()

    # -- Load config --
    cfg = _load_config(config_path)
    engine_cfg = cfg.get("engine", {})
    output_cfg = cfg.get("output", {})
    voices_cfg = cfg.get("voices", {})

    resolved_engine = engine or engine_cfg.get("type", "qwen3")
    resolved_mode = mode or engine_cfg.get("mode", "builtin")
    resolved_output_dir = Path(
        output_dir or output_cfg.get("dir", "render_output")
    )
    resolved_per_line_format = output_cfg.get(
        "per_line_format", "{speaker_id:02d}_{lang}_{line_number:04d}.wav"
    )
    resolved_joined_name = output_cfg.get("joined", "joined.mp3")
    resolved_bitrate = bitrate or output_cfg.get("bitrate", "64k")
    resolved_pause = (
        inter_line_pause_ms
        if inter_line_pause_ms is not None
        else output_cfg.get("inter_line_pause_ms", 300)
    )

    # -- Parse script --
    script_path = Path(script_path)
    if not script_path.exists():
        raise FileNotFoundError(f"Script not found: {script_path}")

    script_text = script_path.read_text(encoding="utf-8")
    try:
        blocks = parse_sentence_format(script_text)
    except SentenceFormatError as e:
        raise ValueError(
            f"Failed to parse script '{script_path}': {e}"
        ) from e

    if not blocks:
        raise ValueError(f"Script '{script_path}' contains no valid sentence blocks")

    # -- Resolve speaker map --
    speaker_map = _resolve_speaker_map(voices_cfg, resolved_mode)
    _check_speaker_coverage(blocks, speaker_map)

    # -- Service lifecycle --
    if service_manager is not None and resolved_engine == "qwen3":
        service_manager.stop_if_running("llm")
        service_manager.start_if_needed("tts")

    # -- Generate audio --
    _log.info(
        "event=render_start engine=%s mode=%s blocks=%d speakers=%s",
        resolved_engine,
        resolved_mode,
        len(blocks),
        sorted(speaker_map.keys()),
    )

    results = generate_sentence_audio(
        blocks,
        speaker_map,
        engine=resolved_engine,
        mode=resolved_mode,
        service_manager=service_manager,
    )

    # -- Write per-line files --
    per_line_files = _write_per_line_audio(
        results,
        resolved_output_dir,
        resolved_per_line_format,
    )

    # -- Build joined MP3 --
    joined_path = resolved_output_dir / resolved_joined_name
    _build_joined_mp3(
        results,
        joined_path,
        resolved_pause,
        resolved_bitrate,
    )

    duration_s = len(results[-1][1]) / 1000.0  # last audio segment
    for _, seg in results:
        # The joined file is the real duration reference
        pass
    joined_seg = AudioSegment.from_file(str(joined_path))
    joined_duration_s = round(len(joined_seg) / 1000.0, 1)

    # -- Stop omnivoice if we started it --
    if service_manager is not None and resolved_engine == "omnivoice":
        service_manager.stop_if_running("tts")

    total_ms = int((time.time() - t_start) * 1000)
    _log.info(
        "event=render_complete lines=%d per_line=%d joined=%.1fs duration_ms=%d",
        len(blocks),
        len(per_line_files),
        joined_duration_s,
        total_ms,
    )

    return {
        "script_path": str(script_path.resolve()),
        "config_path": str(config_path.resolve()) if config_path else str(_DEFAULT_CONFIG_PATH),
        "engine": resolved_engine,
        "mode": resolved_mode,
        "blocks": len(blocks),
        "per_line_files": per_line_files,
        "joined_path": joined_path,
        "joined_duration_s": joined_duration_s,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Render a sentence-format script to per-line WAV files and a joined MP3.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python -m storyline.audio.sentence_render script.txt\n"
            "  python -m storyline.audio.sentence_render script.txt --config my.toml\n"
            "  python -m storyline.audio.sentence_render script.txt -o output --engine omnivoice\n"
            "\n"
            "See config/render.toml for the default configuration.\n"
        ),
    )
    parser.add_argument(
        "script",
        type=str,
        help="Path to a sentence-format script",
    )
    parser.add_argument(
        "--config", "-c",
        type=str,
        default=None,
        help="Path to render TOML config (default: config/render.toml)",
    )
    parser.add_argument(
        "--output-dir", "-o",
        type=str,
        default=None,
        help="Override output directory from config",
    )
    parser.add_argument(
        "--engine",
        type=str,
        choices=["qwen3", "omnivoice"],
        default=None,
        help="Override TTS backend from config",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["builtin", "clone"],
        default=None,
        help="Override voice mode from config",
    )
    parser.add_argument(
        "--pause",
        type=int,
        default=None,
        help="Override inter-line pause (ms) from config",
    )
    parser.add_argument(
        "--bitrate",
        type=str,
        default=None,
        help="Override MP3 bitrate from config (e.g. 128k)",
    )
    parser.add_argument(
        "--list-speakers",
        action="store_true",
        help="Parse the script and list referenced speakers without rendering",
    )
    return parser


def main():
    init_logging()
    parser = _build_parser()
    args = parser.parse_args()

    # -- Quick info mode --
    if args.list_speakers:
        script_text = Path(args.script).read_text(encoding="utf-8")
        try:
            blocks = parse_sentence_format(script_text)
        except SentenceFormatError as e:
            print(f"Parse error: {e}")
            return 1
        if not blocks:
            print("No sentence blocks found.")
            return 1
        speakers = sorted({b.speaker_id for b in blocks})
        lang_speakers = sorted(
            {(b.speaker_id, b.lang) for b in blocks}
        )
        print(f"Lines:           {len(blocks)}")
        print(f"Speaker IDs:     {speakers}")
        print(f"Speaker×language pairs:")
        for sid, lang in lang_speakers:
            print(f"  Speaker {sid} ({lang})")
        return 0

    # -- Render --
    try:
        result = render_sentences(
            args.script,
            config_path=args.config,
            output_dir=args.output_dir,
            engine=args.engine,
            mode=args.mode,
            inter_line_pause_ms=args.pause,
            bitrate=args.bitrate,
        )
    except (FileNotFoundError, ValueError, SentenceFormatError) as e:
        print(f"Error: {e}")
        return 1

    print()
    print(f"Engine:          {result['engine']}")
    print(f"Mode:            {result['mode']}")
    print(f"Script:          {result['script_path']}")
    print(f"Config:          {result['config_path']}")
    print(f"Blocks:          {result['blocks']}")
    print(f"Per-line files:  {len(result['per_line_files'])}")
    print(f"  -> {result['per_line_files'][0].parent}")
    print(f"Joined MP3:      {result['joined_path']}")
    print(f"  -> {result['joined_duration_s']}s")
    return 0


if __name__ == "__main__":
    exit(main())