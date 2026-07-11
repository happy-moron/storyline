#!/usr/bin/env python3
"""Manual E2E test for create_book pipeline with gossie.txt.

Tests each pipeline step individually, verifies audio.toml config loading,
validates TTS service integration, and runs the full E2E pipeline.

Usage:
    . .venv/bin/activate
    python tests/manual_test_e2e.py [--skip-llm] [--skip-tts] [--clean] [--keep-output]
    python tests/manual_test_e2e.py --only config
    python tests/manual_test_e2e.py --only tts
    python tests/manual_test_e2e.py --only split
    python tests/manual_test_e2e.py --only audio
    python tests/manual_test_e2e.py --only pipeline
    python tests/manual_test_e2e.py --only verify
"""

import sys
import os
import time
import json
import shutil
import argparse
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from learnbook.services.manager import ServiceManager, ServiceStatus
from learnbook.audio.audiobook_gen_base import load_profile_from_toml
from learnbook.audio.audiobook_gen_qwen3 import Qwen3TTSService, generate_tts_audio, process_json_to_audio
from learnbook.book.split_text import split_text

INPUT_TEXT = "books/childrens/gossie.txt"
AUTHOR = "childrens"
BOOK = "gossie"
BASE_DIR = f"books/{AUTHOR}/{BOOK}"
AUDIOBOOK_DIR = f"/home/zspdude/temp/audio/{BOOK}"
AUDIO_TOML_PATH = Path("src/learnbook/config/audio.toml")


def print_section(title):
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def print_result(passed, msg=""):
    symbol = "✓" if passed else "✗"
    print(f"  {symbol} {msg}")


def clean_output():
    """Remove existing output directories to force re-processing."""
    for d in [
        f"{BASE_DIR}/split",
        f"{BASE_DIR}/json",
        f"{BASE_DIR}/audio",
        AUDIOBOOK_DIR,
    ]:
        if Path(d).exists():
            print(f"  Removing {d}")
            shutil.rmtree(d)


# ---------------------------------------------------------------------------
# Test 1: audio.toml config loading
# ---------------------------------------------------------------------------
def test_audio_toml_config():
    print_section("Test 1: audio.toml Config Loading")
    errors = []

    # 1a. Parse the TOML directly and validate structure
    with AUDIO_TOML_PATH.open("rb") as f:
        data = tomllib.load(f)

    print("\n  [TOML structure]")
    voices = data.get("voices", {})
    profiles = data.get("profiles", {})
    print(f"    voices keys:   {list(voices.keys())}")
    print(f"    profiles keys: {list(profiles.keys())}")

    if not voices:
        errors.append("No [voices] section in audio.toml")
    if not profiles:
        errors.append("No [profiles] section in audio.toml")

    # 1b. Validate every voice entry has 'text' and 'audio' and audio file exists
    print("\n  [Voice entries]")
    for lang_key, lang_val in voices.items():
        if not isinstance(lang_val, dict):
            errors.append(f"voices.{lang_key} is not a table")
            continue
        for voice_name, voice_data in lang_val.items():
            if not isinstance(voice_data, dict):
                errors.append(f"voices.{lang_key}.{voice_name} is not a table")
                continue
            text_val = voice_data.get("text", "")
            audio_val = voice_data.get("audio", "")
            audio_exists = Path(audio_val).exists() if audio_val else False
            status = "✓" if audio_exists else "✗ MISSING"
            print(f"    voices.{lang_key}.{voice_name}:")
            print(f"      text:  {text_val[:60]}...")
            print(f"      audio: {audio_val}  [{status}]")
            if not audio_val:
                errors.append(f"voices.{lang_key}.{voice_name} missing 'audio' key")
            elif not audio_exists:
                errors.append(f"voices.{lang_key}.{voice_name} audio file not found: {audio_val}")
            if not text_val:
                errors.append(f"voices.{lang_key}.{voice_name} missing 'text' key")

    # 1c. Validate default profile
    print("\n  [Default profile]")
    default_profile = profiles.get("default", {})
    sequence = default_profile.get("sequence", [])
    pause_ms = default_profile.get("pause_ms", 300)
    speed = default_profile.get("speed", 1.0)
    print(f"    sequence: {sequence}")
    print(f"    pause_ms: {pause_ms}")
    print(f"    speed:    {speed}")

    if not sequence:
        errors.append("Profile 'default' has empty or missing sequence")
    if not isinstance(pause_ms, (int, float)):
        errors.append(f"pause_ms is not numeric: {pause_ms!r}")
    if not isinstance(speed, (int, float)):
        errors.append(f"speed is not numeric: {speed!r}")

    # Verify each sequence entry resolves to a valid voice
    for voice_key in sequence:
        parts = voice_key.split(".")
        resolved = data.get("voices", {})
        for part in parts:
            resolved = resolved.get(part, {})
        if not resolved:
            errors.append(f"Profile sequence entry '{voice_key}' does not resolve in [voices]")

    # 1d. Load via the actual function used by the pipeline
    print("\n  [load_profile_from_toml('default')]")
    try:
        ref_audio, ref_text, loaded_pause_ms, loaded_speed = load_profile_from_toml("default")
    except Exception as e:
        print_result(False, f"load_profile_from_toml raised: {e}")
        return False

    for lang in ("chinese", "english"):
        if lang not in ref_audio:
            errors.append(f"ref_audio missing '{lang}' key")
        if lang not in ref_text:
            errors.append(f"ref_text missing '{lang}' key")

    print(f"    ref_audio.chinese:  {ref_audio.get('chinese', 'MISSING')}")
    print(f"    ref_audio.english:  {ref_audio.get('english', 'MISSING')}")
    print(f"    ref_text.chinese:   {ref_text.get('chinese', 'MISSING')[:60]}...")
    print(f"    ref_text.english:   {ref_text.get('english', 'MISSING')[:60]}...")
    print(f"    pause_ms:           {loaded_pause_ms}")
    print(f"    speed:              {loaded_speed}")

    # Verify ref_audio files exist
    for lang, path in ref_audio.items():
        if path and not Path(path).exists():
            errors.append(f"ref_audio[{lang}] file not found: {path}")

    # Verify loaded values match TOML values
    if loaded_pause_ms != pause_ms:
        errors.append(f"pause_ms mismatch: toml={pause_ms}, loaded={loaded_pause_ms}")
    if loaded_speed != speed:
        errors.append(f"speed mismatch: toml={speed}, loaded={loaded_speed}")

    # 1e. Invalid profile should raise KeyError
    try:
        load_profile_from_toml("nonexistent_profile")
        errors.append("load_profile_from_toml should raise KeyError for invalid profile")
    except KeyError:
        pass

    if errors:
        for e in errors:
            print_result(False, e)
        return False

    print_result(True, "audio.toml config loaded and validated correctly")
    return True


# ---------------------------------------------------------------------------
# Test 2: TTS service health & generation
# ---------------------------------------------------------------------------
def test_tts_service(manager):
    print_section("Test 2: TTS Service Health & Generation")
    import requests

    # Check/start TTS
    status = manager.get_status("tts")
    print(f"\n  TTS Service Status: {status.value}")

    if status == ServiceStatus.OFFLINE:
        # Make sure LLM is not running (mutual exclusion)
        llm_status = manager.get_status("llm")
        if llm_status == ServiceStatus.ONLINE:
            print("  Stopping LLM service (mutual exclusion with TTS)...")
            manager.stop("llm")
        print("  Starting TTS service...")
        manager.start("tts")
        print("  TTS service started")

    # Health check
    try:
        resp = requests.get("http://127.0.0.1:11433/health", timeout=5)
        if resp.status_code != 200:
            print_result(False, f"Health check failed: {resp.status_code}")
            return False
        print_result(True, f"Health check: {resp.json()}")
    except Exception as e:
        print_result(False, f"Health check error: {e}")
        return False

    # Test Qwen3TTSService class directly - English
    print("\n  Testing Qwen3TTSService.generate_audio (English)...")
    service = Qwen3TTSService()
    try:
        audio = service.generate_audio("Hello world", "english", "")
        duration_ms = len(audio)
        print_result(True, f"English audio: {duration_ms}ms ({duration_ms/1000:.1f}s)")
        if duration_ms < 200:
            print_result(False, f"English audio suspiciously short: {duration_ms}ms")
            return False
    except Exception as e:
        print_result(False, f"English generation failed: {e}")
        return False

    # Test Qwen3TTSService class directly - Chinese
    print("  Testing Qwen3TTSService.generate_audio (Chinese)...")
    try:
        audio = service.generate_audio("你好世界", "chinese", "")
        duration_ms = len(audio)
        print_result(True, f"Chinese audio: {duration_ms}ms ({duration_ms/1000:.1f}s)")
        if duration_ms < 200:
            print_result(False, f"Chinese audio suspiciously short: {duration_ms}ms")
            return False
    except Exception as e:
        print_result(False, f"Chinese generation failed: {e}")
        return False

    # Test generate_tts_audio compatibility function with profile config
    print("  Testing generate_tts_audio with profile config...")
    ref_audio, ref_text, pause_ms, speed = load_profile_from_toml("default")
    try:
        audio = generate_tts_audio(service, "测试语音", ref_audio, ref_text, is_chinese=True)
        duration_ms = len(audio)
        print_result(True, f"generate_tts_audio (zh): {duration_ms}ms ({duration_ms/1000:.1f}s)")
    except Exception as e:
        print_result(False, f"generate_tts_audio failed: {e}")
        return False

    try:
        audio = generate_tts_audio(service, "Test voice", ref_audio, ref_text, is_chinese=False)
        duration_ms = len(audio)
        print_result(True, f"generate_tts_audio (en): {duration_ms}ms ({duration_ms/1000:.1f}s)")
    except Exception as e:
        print_result(False, f"generate_tts_audio failed: {e}")
        return False

    # Verify speaker mapping
    print("\n  Verifying speaker mapping...")
    test_cases = [
        ("chinese", "Vivian"),
        ("zh", "Vivian"),
        ("zh-cn", "Vivian"),
        ("english", "Ryan"),
        ("en", "Ryan"),
        ("en-us", "Ryan"),
        ("french", "Ryan"),  # unknown lang defaults to Ryan
    ]
    for lang_code, expected_speaker in test_cases:
        actual = service._get_speaker_for_lang(lang_code)
        ok = actual == expected_speaker
        print_result(ok, f"_get_speaker_for_lang('{lang_code}') = '{actual}' (expected '{expected_speaker}')")
        if not ok:
            return False

    return True


# ---------------------------------------------------------------------------
# Test 3: Text splitting
# ---------------------------------------------------------------------------
def test_split(clean):
    print_section("Test 3: Text Splitting")
    split_dir = f"{BASE_DIR}/split/source"

    if clean and Path(split_dir).exists():
        print(f"  Cleaning {split_dir}")
        shutil.rmtree(split_dir)

    existing = list(Path(split_dir).glob("*.txt")) if Path(split_dir).exists() else []
    if existing and not clean:
        print(f"\n  Split files already exist ({len(existing)} files), skipping split")
        for f in sorted(existing):
            print(f"    {f.name} ({f.stat().st_size} bytes)")
        print_result(True, "Split files present")
        return True

    if not Path(INPUT_TEXT).exists():
        print_result(False, f"Input file not found: {INPUT_TEXT}")
        return False

    split_text(INPUT_TEXT, split_dir)

    files = sorted(Path(split_dir).glob("*.txt"))
    print(f"\n  Created {len(files)} chunk(s):")
    for f in files:
        content = f.read_text()[:120].replace("\n", "\\n")
        print(f"    {f.name} ({f.stat().st_size} bytes): {content}...")

    if not files:
        print_result(False, "No split files created")
        return False

    # Gossie is short (~700 chars), should fit in a single chunk (2000 char limit)
    if len(files) != 1:
        print(f"  Warning: expected 1 chunk for gossie.txt, got {len(files)}")

    print_result(True, "Text splitting works")
    return True


# ---------------------------------------------------------------------------
# Test 4: Audio generation from existing JSON
# ---------------------------------------------------------------------------
def test_audio_gen_from_json(manager, clean):
    print_section("Test 4: Audio Generation from JSON")

    json_source_dir = f"{BASE_DIR}/json/source"
    json_files = sorted(Path(json_source_dir).glob("*.json"))

    if not json_files:
        print(f"  ✗ No JSON files found in {json_source_dir}")
        print("  Run the full pipeline first (or at least through translate step)")
        return False

    test_json = json_files[0]
    test_output = f"/tmp/test_gossie_audio_{int(time.time())}.mp3"

    print(f"\n  Input:  {test_json}")
    print(f"  Output: {test_output}")

    # Validate JSON structure
    with open(test_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print_result(False, f"Expected JSON array, got {type(data).__name__}")
        return False

    print(f"  JSON contains {len(data)} sentences")
    for i, s in enumerate(data[:3]):
        zh = s.get("chinese", "")[:50]
        en = s.get("english", "")[:50]
        print(f"    [{i}] zh: {zh}... | en: {en}...")
    if len(data) > 3:
        print(f"    ... ({len(data) - 3} more)")

    # Validate required keys in each sentence
    for i, s in enumerate(data):
        if "chinese" not in s or "english" not in s:
            print_result(False, f"Sentence {i} missing 'chinese' or 'english' key: {list(s.keys())}")
            return False

    # Ensure TTS is running
    status = manager.get_status("tts")
    if status == ServiceStatus.OFFLINE:
        llm_status = manager.get_status("llm")
        if llm_status == ServiceStatus.ONLINE:
            print("  Stopping LLM service (mutual exclusion with TTS)...")
            manager.stop("llm")
        print("  Starting TTS service...")
        manager.start("tts")

    # Remove existing audiobook output if --clean
    audiobook_path = Path(AUDIOBOOK_DIR) / f"{BOOK}_1.mp3"
    if clean and audiobook_path.exists():
        print(f"  Removing existing audiobook: {audiobook_path}")
        audiobook_path.unlink()

    # Use a temp dir for standalone files (matching how create_book.py calls it)
    standalone_dir = f"/tmp/test_gossie_standalone_{int(time.time())}"
    os.makedirs(standalone_dir, exist_ok=True)
    standalone_prefix = f"{standalone_dir}/{BOOK}_1"

    try:
        start = time.time()
        process_json_to_audio(
            str(test_json),
            test_output,
            profile_key="default",
            standalone_file=standalone_prefix,
        )
        elapsed = time.time() - start

        if not Path(test_output).exists():
            print_result(False, f"Output file not created: {test_output}")
            return False

        size = Path(test_output).stat().st_size
        print(f"\n  Audio generated in {elapsed:.1f}s")
        print(f"    File: {test_output} ({size} bytes, {size/1024:.1f} KB)")

        if size < 1024:
            print_result(False, f"Audio file suspiciously small ({size} bytes)")
            return False

        # Load and check duration
        from pydub import AudioSegment
        audio = AudioSegment.from_file(test_output)
        duration_s = len(audio) / 1000
        print(f"    Duration: {duration_s:.1f}s")
        print(f"    Channels: {audio.channels}")
        print(f"    Sample rate: {audio.frame_rate}Hz")

        # With the repetition pattern (slow zh + en + med zh + en + normal zh + en),
        # each sentence should produce at least ~5s of audio
        expected_min_duration = len(data) * 5
        if duration_s < expected_min_duration:
            print_result(False, f"Audio too short: {duration_s:.1f}s < expected ~{expected_min_duration}s")
            return False

        # Verify standalone per-sentence files were created
        standalone_files = sorted(Path(standalone_dir).glob("*.mp3"))
        print(f"\n  Standalone files: {len(standalone_files)} created")
        for sf in standalone_files[:5]:
            sf_size = sf.stat().st_size
            print(f"    {sf.name} ({sf_size} bytes)")
        if len(standalone_files) > 5:
            print(f"    ... ({len(standalone_files) - 5} more)")

        if len(standalone_files) != len(data):
            print_result(False, f"Expected {len(data)} standalone files, got {len(standalone_files)}")
            return False

        for sf in standalone_files:
            if sf.stat().st_size < 512:
                print_result(False, f"Standalone file suspiciously small: {sf.name} ({sf.stat().st_size} bytes)")
                return False

        print_result(True, f"Audio generation works ({duration_s:.1f}s, {size/1024:.1f}KB, {len(standalone_files)} standalone files)")
        return True

    except Exception as e:
        print_result(False, f"Audio generation failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if Path(test_output).exists():
            Path(test_output).unlink()
        if Path(standalone_dir).exists():
            shutil.rmtree(standalone_dir)


# ---------------------------------------------------------------------------
# Test 5: Full create_book pipeline
# ---------------------------------------------------------------------------
def test_full_pipeline(manager, skip_llm, skip_tts, clean):
    print_section("Test 5: Full create_book Pipeline")

    from learnbook.book.create_book import create_book

    # Load task profiles from config
    config_path = Path("src/learnbook/config/llms_for_tasks.toml")
    try:
        with config_path.open("rb") as f:
            cfg = tomllib.load(f)
        llm_cfg = cfg.get("llm", {})
        model_id = llm_cfg.get("model_id", "local-llamacpp")
        models = [model_id]
        default_profile = cfg.get("default", {}).get("profile")
        task_profiles = {
            task: cfg.get(task, {}).get("profile", default_profile)
            for task in ("translate", "tokenize", "dictionary")
        }
        print(f"\n  Task profiles from config:")
        for task, profile in task_profiles.items():
            print(f"    {task}: profile={profile}")
        print(f"    model_id: {model_id}")
    except Exception as e:
        print(f"  Warning: could not load llms_for_tasks.toml: {e}")
        models = []
        task_profiles = {}

    skip_audio = skip_tts
    skip_simplify = True  # Children's books are already simple

    print(f"\n  Input:          {INPUT_TEXT}")
    print(f"  Author:         {AUTHOR}")
    print(f"  Max chunks:     1")
    print(f"  Skip simplify:  {skip_simplify}")
    print(f"  Skip audio:     {skip_audio}")
    print(f"  Skip LLM:       {skip_llm}")

    if not Path(INPUT_TEXT).exists():
        print_result(False, f"Input file not found: {INPUT_TEXT}")
        return False

    if skip_llm and skip_tts:
        print("\n  Nothing to test (both --skip-llm and --skip-tts specified)")
        return True

    try:
        start = time.time()
        create_book(
            INPUT_TEXT,
            AUTHOR,
            max_chunks=1,
            skip_simplify=skip_simplify,
            models=models,
            skip_audio=skip_audio,
            service_manager=manager if not skip_llm else None,
            task_profiles=task_profiles,
        )
        elapsed = time.time() - start
        print_result(True, f"Pipeline completed in {elapsed:.1f}s")
        return True
    except Exception as e:
        print_result(False, f"Pipeline failed: {e}")
        import traceback
        traceback.print_exc()
        return False


# ---------------------------------------------------------------------------
# Test 6: Verify output files
# ---------------------------------------------------------------------------
def verify_outputs(skip_tts):
    print_section("Test 6: Verify Output Files")

    expected_dirs = {
        "split/source": True,
        "json/source": True,
        "json/tokenized": True,
    }
    if not skip_tts:
        expected_dirs["audio"] = True

    all_ok = True

    for subdir, required in expected_dirs.items():
        dir_path = Path(BASE_DIR) / subdir
        if not dir_path.exists():
            if required:
                print_result(False, f"Directory missing: {dir_path}")
                all_ok = False
            continue

        actual = sorted(dir_path.iterdir())
        print(f"\n  {dir_path}/")
        if not actual:
            print(f"    (empty)")
            if required:
                print_result(False, f"No files in required directory: {dir_path}")
                all_ok = False
            continue

        for f in actual:
            size = f.stat().st_size
            print(f"    {f.name} ({size} bytes, {size/1024:.1f} KB)")

    # Validate source JSON (has chinese/english keys)
    source_json = Path(BASE_DIR) / "json/source" / f"{BOOK}_1.json"
    if source_json.exists():
        with open(source_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print_result(False, f"{source_json}: expected JSON array, got {type(data).__name__}")
            all_ok = False
        else:
            print(f"\n  {source_json}: {len(data)} sentences")
            for i, s in enumerate(data[:3]):
                zh = s.get("chinese", "")[:50]
                en = s.get("english", "")[:50]
                print(f"    [{i}] zh: {zh}... | en: {en}...")
            if len(data) > 3:
                print(f"    ... ({len(data) - 3} more)")
            for i, s in enumerate(data):
                if "chinese" not in s or "english" not in s:
                    print_result(False, f"{source_json} sentence {i} missing keys: {list(s.keys())}")
                    all_ok = False
                    break
    else:
        print_result(False, f"Missing: {source_json}")
        all_ok = False

    # Validate tokenized JSON (has 't' key with token arrays)
    token_json = Path(BASE_DIR) / "json/tokenized" / f"{BOOK}_1.json"
    if token_json.exists():
        with open(token_json, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            print_result(False, f"{token_json}: expected JSON array, got {type(data).__name__}")
            all_ok = False
        else:
            print(f"\n  {token_json}: {len(data)} sentences")
            for i, s in enumerate(data[:3]):
                t_count = len(s.get("t", []))
                print(f"    [{i}] tokens: {t_count}")
            if len(data) > 3:
                print(f"    ... ({len(data) - 3} more)")
            for i, s in enumerate(data):
                if "t" not in s:
                    print_result(False, f"{token_json} sentence {i} missing 't' key: {list(s.keys())}")
                    all_ok = False
                    break
                if not isinstance(s["t"], list):
                    print_result(False, f"{token_json} sentence {i} 't' is not a list")
                    all_ok = False
                    break
    else:
        print_result(False, f"Missing: {token_json}")
        all_ok = False

    # Check audiobook output
    if not skip_tts:
        audiobook_path = Path(AUDIOBOOK_DIR) / f"{BOOK}_1.mp3"
        if audiobook_path.exists():
            size = audiobook_path.stat().st_size
            print(f"\n  Audiobook: {audiobook_path} ({size} bytes, {size/1024:.1f} KB)")
            if size < 1024:
                print_result(False, "Audiobook suspiciously small")
                all_ok = False
            else:
                from pydub import AudioSegment
                audio = AudioSegment.from_file(str(audiobook_path))
                print(f"    Duration: {len(audio)/1000:.1f}s")
                print_result(True, f"Audiobook valid ({len(audio)/1000:.1f}s)")
        else:
            print_result(False, f"Audiobook missing: {audiobook_path}")
            all_ok = False

        # Check standalone per-sentence audio files
        audio_dir = Path(BASE_DIR) / "audio"
        if audio_dir.exists():
            standalone_files = sorted(audio_dir.glob("*.mp3"))
            print(f"\n  Standalone audio files: {len(standalone_files)}")
            for f in standalone_files[:5]:
                size = f.stat().st_size
                print(f"    {f.name} ({size} bytes)")
            if len(standalone_files) > 5:
                print(f"    ... ({len(standalone_files) - 5} more)")

    # Check dictionary was updated (step 6 now runs regardless of skip_audio)
    dict_path = Path("dict/custom_dict.json")
    if dict_path.exists():
        with open(dict_path, "r", encoding="utf-8") as f:
            dict_data = json.load(f)
        if isinstance(dict_data, dict):
            print(f"\n  Dictionary: {len(dict_data)} entries in custom_dict.json")
            # Check if any gossie-related entries exist (only if pipeline ran with LLM)
            gossie_words = [w for w in dict_data if any(
                isinstance(v, dict) and "gossie" in str(v).lower()
                for v in [dict_data[w]]
            )]
            if gossie_words:
                print(f"    Gossie-related entries: {len(gossie_words)}")
                for w in gossie_words[:3]:
                    print(f"      {w}")
            else:
                print("    No gossie-related entries found (run pipeline with LLM to populate)")
        else:
            print(f"\n  Dictionary: unexpected type {type(dict_data).__name__}")
    else:
        print_result(False, f"Dictionary missing: {dict_path}")
        all_ok = False

    if all_ok:
        print_result(True, "All output files verified")
    return all_ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Manual E2E test for create_book pipeline")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM-dependent steps (translate, tokenize, dict)")
    parser.add_argument("--skip-tts", action="store_true",
                        help="Skip TTS/audio generation")
    parser.add_argument("--clean", action="store_true",
                        help="Remove existing output before running to force re-processing")
    parser.add_argument("--keep-output", action="store_true",
                        help="Don't clean up output files after test")
    parser.add_argument("--only",
                        choices=["config", "tts", "split", "audio", "pipeline", "verify"],
                        help="Run only one test section")
    args = parser.parse_args()

    print("\n" + "=" * 70)
    print("  MANUAL E2E TEST FOR CREATE_BOOK PIPELINE")
    print(f"  Source: {INPUT_TEXT}")
    print(f"  Clean:  {args.clean}")
    print("=" * 70)

    if args.clean:
        print_section("Pre-test cleanup")
        clean_output()

    manager = ServiceManager()
    results = {}

    # Test 1: Config loading (always runs, no services needed)
    if not args.only or args.only == "config":
        results["audio_toml_config"] = test_audio_toml_config()

    # Test 2: TTS service
    if not args.only or args.only == "tts":
        if not args.skip_tts:
            results["tts_service"] = test_tts_service(manager)
        else:
            print("\n  (Skipping TTS test --skip-tts)")

    # Test 3: Split
    if not args.only or args.only == "split":
        results["text_split"] = test_split(clean=args.clean)

    # Test 4: Audio gen from existing JSON
    if not args.only or args.only == "audio":
        if not args.skip_tts:
            results["audio_gen"] = test_audio_gen_from_json(manager, clean=args.clean)
        else:
            print("\n  (Skipping audio gen test --skip-tts)")

    # Test 5: Full pipeline
    if not args.only or args.only == "pipeline":
        results["full_pipeline"] = test_full_pipeline(
            manager, args.skip_llm, args.skip_tts, clean=args.clean
        )

    # Test 6: Verify outputs
    if not args.only or args.only == "verify":
        results["verify_outputs"] = verify_outputs(args.skip_tts)

    # Cleanup services
    if not args.skip_tts:
        try:
            manager.stop_if_running("tts")
        except Exception:
            pass

    # Summary
    print_section("TEST SUMMARY")
    all_passed = all(results.values()) if results else True
    for name, passed in results.items():
        status = "✓ PASSED" if passed else "✗ FAILED"
        print(f"  {name}: {status}")

    print("\n" + "=" * 70)
    if all_passed:
        print("  ✓ ALL TESTS PASSED")
    else:
        print("  ✗ SOME TESTS FAILED")
    print("=" * 70)

    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
