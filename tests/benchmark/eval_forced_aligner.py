#!/usr/bin/env python3
"""
Standalone evaluation harness for the ForcedAligner used in the create_book
audio pipeline.

For each test sentence (with a known expected word count), this script:
  1. Runs the sentence through TTS (Qwen3TTSService.generate_audio /
     generate_voice_clone — same code path as the production pipeline).
  2. Runs the resulting audio through forced alignment.
  3. Logs the aligner input/output in a compact, greppable format for
     debugging, e.g.:
         words: The quick brown fox jumped over the lazy dogs.
         times: [0.24-0.58, 0.62-0.81, 0.85-1.10, ...]
  4. Validates that the number of word entries returned by the aligner
     matches the expected word count for the sentence, and flags mismatches.

Test sentence file format (one sentence per line):
    <expected_word_count>:<sentence text>
e.g.
    9:The quick brown fox jumped over the lazy dogs.

Lines that are blank or start with '#' are ignored.

Usage:
    python eval_forced_aligner.py --sentences forced_aligner_test_sentences.txt

    # English only, first 10 sentences, quiet mode (summary only):
    python eval_forced_aligner.py --sentences sentences.txt --limit 10 --quiet

    # Mandarin:
    python eval_forced_aligner.py --sentences zh_sentences.txt --language zh

Exit code is 0 if all sentences passed, 1 otherwise (so it can be wired
into CI).
"""

from __future__ import annotations

import argparse
import importlib
import json
import logging
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path

_log = logging.getLogger("eval_forced_aligner")


# ---------------------------------------------------------------------------
# Locate the pipeline package + config regardless of where this script lives
# or how the 'storyline' package is internally laid out. We don't hardcode a
# module path -- instead we walk the installed 'storyline' package looking
# for a module that defines Qwen3TTSService, and separately locate
# audio.toml. Both can be overridden explicitly via --tts-module /
# --config-dir if the auto-search picks the wrong one (e.g. more than one
# audio.toml exists in the repo).
# ---------------------------------------------------------------------------

def _ensure_storyline_importable() -> None:
    """Walk up from this file looking for a directory containing 'storyline/',
    and add it to sys.path so `import storyline` works even when this script
    is run directly (not via -m) from an arbitrary subdirectory like tests/."""
    try:
        import storyline  # noqa: F401
        return
    except ImportError:
        pass

    here = Path(__file__).resolve()
    for candidate in [here.parent, *here.parents]:
        if (candidate / "storyline").is_dir():
            sys.path.insert(0, str(candidate))
            return
    if (Path.cwd() / "storyline").is_dir():
        sys.path.insert(0, str(Path.cwd()))


def _iter_storyline_module_names():
    """Walk the 'storyline' package on disk (via file paths, not pkgutil's
    package-walker) and yield dotted module names for every .py file found.
    This is done via a plain filesystem walk rather than pkgutil.walk_packages
    because walk_packages only recurses into a subdirectory it has already
    successfully imported *as a package*, which is unreliable for implicit
    namespace packages (subdirectories with no __init__.py)."""
    import storyline
    seen = set()
    for root in storyline.__path__:
        root_path = Path(root)
        for py_file in root_path.rglob("*.py"):
            if py_file.name == "__init__.py":
                rel_parts = py_file.parent.relative_to(root_path).parts
            else:
                rel_parts = py_file.relative_to(root_path).with_suffix("").parts
            if any(not p.isidentifier() for p in rel_parts):
                continue  # skip non-importable names (e.g. dirs with dashes/spaces)
            module_name = ".".join(("storyline", *rel_parts))
            if module_name not in seen:
                seen.add(module_name)
                yield module_name


def _find_service_class(explicit_module):
    """Return the Qwen3TTSService class, either from an explicit module path
    or by scanning the 'storyline' package for the first module defining it."""
    if explicit_module:
        mod = importlib.import_module(explicit_module)
        if not hasattr(mod, "Qwen3TTSService"):
            raise ImportError(f"Module '{explicit_module}' has no Qwen3TTSService class.")
        return mod.Qwen3TTSService

    tried = 0
    failures = []
    for module_name in _iter_storyline_module_names():
        tried += 1
        try:
            mod = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - record and keep scanning
            failures.append((module_name, exc))
            continue
        if hasattr(mod, "Qwen3TTSService"):
            _log.info("Found Qwen3TTSService in %s", module_name)
            return mod.Qwen3TTSService

    lines = [
        "Could not find a Qwen3TTSService class anywhere under the 'storyline' package "
        f"(scanned {tried} modules).",
        "Pass --tts-module explicitly, e.g.: --tts-module storyline.audio.audiobook_gen_qwen3",
    ]
    if failures:
        lines.append(f"{len(failures)} module(s) failed to import during the scan (likely unrelated "
                      "to this tool); first few, for debugging:")
        for name, exc in failures[:5]:
            lines.append(f"  {name}: {exc!r}")
    raise ImportError("\n".join(lines))


def _find_config_dir(explicit_dir):
    """Return the directory containing audio.toml, either from an explicit
    path or by searching the 'storyline' package tree for it."""
    if explicit_dir:
        d = Path(explicit_dir)
        if not (d / "audio.toml").exists():
            raise FileNotFoundError(f"No audio.toml found in {d}")
        return d

    import storyline
    root = Path(storyline.__file__).resolve().parent
    matches = sorted(root.rglob("audio.toml"))
    if not matches:
        raise FileNotFoundError(
            "Could not find audio.toml anywhere under the 'storyline' package. "
            "Pass --config-dir explicitly."
        )
    if len(matches) > 1:
        _log.warning(
            "Multiple audio.toml files found; using %s. Pass --config-dir to pick a different one.\n  %s",
            matches[0], "\n  ".join(str(m) for m in matches),
        )
    return matches[0].parent


# ---------------------------------------------------------------------------
# Voice config loading (mirrors chunk_audio_gen._get_chunk_voice, kept
# standalone so this harness has no dependency on the chunking pipeline).
# ---------------------------------------------------------------------------

def _load_audio_toml(config_dir: Path) -> dict:
    with (config_dir / "audio.toml").open("rb") as f:
        return tomllib.load(f)


def get_voice(config_dir: Path, language: str, profile: str = "default") -> dict:
    audio_toml = _load_audio_toml(config_dir)
    voices = audio_toml["voices"]
    seq = audio_toml["profiles"][profile]["sequence"]
    for step in seq:
        if step["lang"] == language:
            voice = dict(voices[step["voice"]])
            voice["voice_name"] = step["voice"]
            return voice
    raise KeyError(f"No voice for language '{language}' in profile '{profile}'")


def generate_tts(service, text: str, voice: dict, instruct: str = ""):
    """Generate audio for `text` using whichever mode the voice is configured for."""
    mode = voice["mode"]
    language = voice["language"]
    if mode == "custom_voice":
        return service.generate_audio(text, language, speaker=voice["speaker"], instruct=instruct)
    elif mode == "voice_clone":
        return service.generate_voice_clone(
            text, language, ref_audio_path=voice["ref_audio"], ref_text=voice["ref_text"]
        )
    else:
        raise ValueError(f"Unknown voice mode: {mode}")


# ---------------------------------------------------------------------------
# Test case / result data structures
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    line_no: int
    expected_count: int
    text: str


@dataclass
class TestResult:
    case: TestCase
    words: list
    audio_duration: float
    gen_seconds: float
    align_seconds: float
    error: str | None = None

    @property
    def actual_count(self) -> int:
        return len(self.words)

    @property
    def passed(self) -> bool:
        return self.error is None and self.actual_count == self.case.expected_count


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_test_sentences(path: Path) -> list[TestCase]:
    cases = []
    for i, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"{path}:{i}: expected 'N:sentence' format, got: {raw!r}")
        count_str, text = line.split(":", 1)
        count_str = count_str.strip()
        text = text.strip()
        if not count_str.isdigit():
            raise ValueError(f"{path}:{i}: expected numeric word count, got: {count_str!r}")
        cases.append(TestCase(line_no=i, expected_count=int(count_str), text=text))
    return cases


# ---------------------------------------------------------------------------
# TTS + alignment run
# ---------------------------------------------------------------------------

def run_one(service, case: TestCase, voice: dict, language: str) -> TestResult:
    t0 = time.monotonic()
    try:
        audio = generate_tts(service, case.text, voice)
    except Exception as exc:  # noqa: BLE001 - want to record any TTS failure as a result
        return TestResult(
            case=case, words=[], audio_duration=0.0,
            gen_seconds=time.monotonic() - t0, align_seconds=0.0,
            error=f"TTS generation failed: {exc!r}",
        )
    gen_seconds = time.monotonic() - t0
    audio_duration = len(audio) / 1000.0

    t1 = time.monotonic()
    try:
        words = service.forced_align(audio, case.text, language)
    except Exception as exc:  # noqa: BLE001
        return TestResult(
            case=case, words=[], audio_duration=audio_duration,
            gen_seconds=gen_seconds, align_seconds=time.monotonic() - t1,
            error=f"Forced alignment failed: {exc!r}",
        )
    align_seconds = time.monotonic() - t1

    return TestResult(
        case=case, words=words, audio_duration=audio_duration,
        gen_seconds=gen_seconds, align_seconds=align_seconds,
    )


# ---------------------------------------------------------------------------
# Logging / reporting
# ---------------------------------------------------------------------------

def format_timestamps(words: list) -> str:
    parts = []
    for w in words:
        s = w.get("start_time")
        e = w.get("end_time")
        if s is None or e is None:
            parts.append("?-?")
        else:
            parts.append(f"{s:.2f}-{e:.2f}")
    return "[" + ", ".join(parts) + "]"


def format_word_texts(words: list) -> str:
    return " ".join(str(w.get("word", "?")) for w in words)


def log_result(result: TestResult) -> None:
    c = result.case
    status = "PASS" if result.passed else "FAIL"
    _log.info("-" * 70)
    _log.info('[%s] #%-3d expected=%-3d actual=%-3d  "%s"', status, c.line_no, c.expected_count, result.actual_count, c.text)
    if result.error:
        _log.info("    ERROR: %s", result.error)
        return
    _log.info("    duration=%.2fs  gen=%.2fs  align=%.2fs", result.audio_duration, result.gen_seconds, result.align_seconds)
    _log.info("    words: %s", format_word_texts(result.words))
    _log.info("    times: %s", format_timestamps(result.words))
    if not result.passed:
        diff = result.actual_count - c.expected_count
        _log.info("    MISMATCH: %+d words vs expected", diff)


def write_jsonl_log(results: list[TestResult], path: Path) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps({
                "line_no": r.case.line_no,
                "text": r.case.text,
                "expected_count": r.case.expected_count,
                "actual_count": r.actual_count,
                "passed": r.passed,
                "error": r.error,
                "audio_duration": r.audio_duration,
                "gen_seconds": r.gen_seconds,
                "align_seconds": r.align_seconds,
                "words": r.words,
            }, ensure_ascii=False) + "\n")


def print_summary(results: list[TestResult]) -> None:
    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed
    errored = sum(1 for r in results if r.error)

    print()
    print("=" * 70)
    print("FORCED ALIGNER EVALUATION SUMMARY")
    print("=" * 70)
    print(f"Total sentences : {total}")
    if total:
        print(f"Passed          : {passed} ({100 * passed / total:.1f}%)")
    print(f"Failed          : {failed}  (of which errored: {errored})")

    if failed:
        print()
        print("Failures:")
        for r in results:
            if r.passed:
                continue
            if r.error:
                print(f'  #{r.case.line_no:>3}  "{r.case.text}"  -> ERROR: {r.error}')
            else:
                print(f'  #{r.case.line_no:>3}  expected={r.case.expected_count:<3} actual={r.actual_count:<3}  "{r.case.text}"')
    print("=" * 70)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate ForcedAligner word-count accuracy via TTS + alignment.")
    parser.add_argument("--sentences", type=Path, required=True, help="Path to test sentences file (N:sentence per line).")
    parser.add_argument("--language", default="en", choices=["en", "zh"], help="Language to synthesize/align.")
    parser.add_argument("--profile", default="default", help="Voice profile name in audio.toml.")
    parser.add_argument("--log-dir", type=Path, default=Path("fa_eval_logs"), help="Directory to write the JSONL results log.")
    parser.add_argument("--limit", type=int, default=None, help="Only run the first N sentences.")
    parser.add_argument("--quiet", action="store_true", help="Suppress per-sentence logging; print summary only.")
    parser.add_argument("--tts-module", default=None, help="Explicit dotted module path containing Qwen3TTSService "
                         "(e.g. storyline.book.audio.audiobook_gen_qwen3). Auto-detected if omitted.")
    parser.add_argument("--config-dir", default=None, help="Explicit directory containing audio.toml. Auto-detected if omitted.")
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING if args.quiet else logging.INFO, format="%(message)s")

    _ensure_storyline_importable()
    tts_service_cls = _find_service_class(args.tts_module)
    config_dir = _find_config_dir(args.config_dir)
    _log.info("Using config dir: %s", config_dir)

    cases = load_test_sentences(args.sentences)
    if args.limit:
        cases = cases[: args.limit]
    if not cases:
        print("No test sentences found.", file=sys.stderr)
        return 1

    voice = get_voice(config_dir, args.language, args.profile)
    service = tts_service_cls()

    args.log_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = args.log_dir / f"forced_aligner_eval_{int(time.time())}.jsonl"

    results = []
    for case in cases:
        result = run_one(service, case, voice, args.language)
        results.append(result)
        log_result(result)

    write_jsonl_log(results, jsonl_path)
    print_summary(results)
    print(f"\nDetailed JSONL log written to: {jsonl_path}")

    return 0 if all(r.passed for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
