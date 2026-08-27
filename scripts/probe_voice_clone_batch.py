#!/usr/bin/env python3
"""Probe Qwen3-TTS voice-clone batch sizes to find the OOM threshold.

Starts the Qwen3-TTS service (stopping the LLM first if it holds the GPU),
then sends increasing batch sizes to ``/voice_clone`` using the same
shared-reference payload that ``Qwen3TTSService.generate_voice_clone_batch``
sends.  The largest batch size that completes is the practical working
threshold for batch mode.

Usage:
    python scripts/probe_voice_clone_batch.py
    python scripts/probe_voice_clone_batch.py --sizes "1,2,4,8,16,24,32"
    python scripts/probe_voice_clone_batch.py --ref-audio voices/student.wav
    python scripts/probe_voice_clone_batch.py --timeout 600
    python scripts/probe_voice_clone_batch.py --restart-between
"""

import argparse
import base64
import re
import sys
import time
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for p in (PROJECT_ROOT / "src", PROJECT_ROOT):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from storyline.podcast.script_parser import parse_script  # noqa: E402
from storyline.services.manager import ServiceManager, ServiceStatus  # noqa: E402

TTS_BASE_URL = "http://127.0.0.1:11433"
HEALTH_URL = f"{TTS_BASE_URL}/health"

VOICES_DIR = PROJECT_ROOT / "voices"
PODCASTS_DIR = PROJECT_ROOT / "books_src" / "podcasts"

LANGUAGE_MAP = {"en": "English", "zh": "Chinese"}

DEFAULT_SIZES = [1, 2, 4, 8, 12, 16, 24, 32, 48, 64, 96, 128]

_DIALOGUE_RE = re.compile(r'^dialogue\s*=\s*"(.*)"\s*$')


def _parse_dialogue(path: Path) -> str:
    content = path.read_text(encoding="utf-8")
    content = (
        content.replace("\u201c", '"').replace("\u201d", '"')
        .replace("\u2018", "'").replace("\u2019", "'")
    )
    for line in content.splitlines():
        m = _DIALOGUE_RE.match(line.strip())
        if m:
            return m.group(1)
    raise ValueError(f"No dialogue field found in: {path}")


def _collect_dialogue_texts() -> list[str]:
    texts: list[str] = []
    if PODCASTS_DIR.exists():
        for path in sorted(PODCASTS_DIR.glob("*.txt")):
            try:
                script = parse_script(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for line in script.dialogue:
                texts.append(line.chinese)
    return texts


_FALLBACK_TEXTS = [
    "欢迎光临！请看菜单。",
    "谢谢。请问，那个人在吃什么？",
    "他在吃牛肉面。",
    "哦，我没有点牛肉面。我想要一碗面条。",
    "还有，我没有水。可以给我一杯水吗？",
    "好的，请稍等。",
    "咱们涂完防晒霜以后，就去游泳吧！",
    "看！海浪以后，有很多小螃蟹！",
    "没问题！我也想在吃东西以前，喝点水。",
    "你学得很快，真的很快。现在，我们来试一点更有挑战性的内容。",
    "只要跟着我的节奏，你一定能拿下。来，深吸一口气，我们开始吧。",
    "我觉得值得好好琢磨一下，你觉得呢？",
]


def _build_batch(texts: list[str], n: int) -> list[str]:
    if not texts:
        texts = _FALLBACK_TEXTS
    return [texts[i % len(texts)] for i in range(n)]


def _health(timeout: int = 5) -> bool:
    try:
        resp = requests.get(HEALTH_URL, timeout=timeout)
        return resp.status_code == 200
    except Exception:
        return False


def _ensure_tts(svc: ServiceManager, force_restart: bool) -> bool:
    if force_restart and svc.get_status("tts") == ServiceStatus.ONLINE:
        print("Restarting TTS service (clean memory state)…", flush=True)
        svc.stop("tts")
    if svc.get_status("tts") != ServiceStatus.ONLINE:
        print("Starting TTS service…", flush=True)
        svc.stop_if_running("llm")  # free the GPU, matching create_mp3
        svc.start("tts")
    return True


def _probe(session, texts, languages, ref_audio_b64, ref_text, timeout):
    payload = {
        "text": texts,
        "language": languages,
        "ref_audio": ref_audio_b64,
        "ref_text": ref_text,
    }
    total_chars = sum(len(t) for t in texts)
    t0 = time.time()
    try:
        resp = session.post(
            f"{TTS_BASE_URL}/voice_clone",
            json=payload,
            timeout=timeout,
        )
    except requests.Timeout:
        return {
            "status": "timeout",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "detail": f"timed out after {timeout}s",
            "total_chars": total_chars,
        }
    except requests.ConnectionError as e:
        return {
            "status": "connection",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "detail": str(e)[:200],
            "total_chars": total_chars,
        }
    except Exception as e:  # pragma: no cover - defensive
        return {
            "status": "error",
            "elapsed_ms": int((time.time() - t0) * 1000),
            "detail": f"{type(e).__name__}: {e}",
            "total_chars": total_chars,
        }

    elapsed_ms = int((time.time() - t0) * 1000)
    if resp.status_code != 200:
        return {
            "status": "http",
            "elapsed_ms": elapsed_ms,
            "detail": f"HTTP {resp.status_code}: ",
            "total_chars": total_chars,
        }

    try:
        data = resp.json()
    except ValueError:
        return {
            "status": "http",
            "elapsed_ms": elapsed_ms,
            "detail": "non-JSON response body",
            "total_chars": total_chars,
        }

    audio = data.get("audio")
    if isinstance(audio, str):
        # The server collapses a length-1 batch into a bare string
        # (see _audio_to_base64), so normalize it to a one-item list.
        audio = [audio]
    if not isinstance(audio, list):
        return {
            "status": "http",
            "elapsed_ms": elapsed_ms,
            "detail": f"audio field is {type(audio).__name__}, expected list",
            "total_chars": total_chars,
        }
    if len(audio) != len(texts):
        return {
            "status": "http",
            "elapsed_ms": elapsed_ms,
            "detail": f"expected {len(texts)} audio items, got {len(audio)}",
            "total_chars": total_chars,
        }
    if any(not isinstance(b, str) or not b for b in audio):
        return {
            "status": "http",
            "elapsed_ms": elapsed_ms,
            "detail": "one or more empty audio payloads",
            "total_chars": total_chars,
        }

    return {
        "status": "ok",
        "elapsed_ms": elapsed_ms,
        "items": len(audio),
        "total_chars": total_chars,
    }


def _parse_sizes(raw: str | None) -> list[int]:
    if not raw:
        return list(DEFAULT_SIZES)
    tokens = re.split(r"[\s,]+", raw.strip())
    sizes = []
    for tok in tokens:
        if not tok:
            continue
        try:
            n = int(tok)
        except ValueError:
            raise argparse.ArgumentTypeError(f"Invalid batch size: {tok!r}")
        if n < 1:
            raise argparse.ArgumentTypeError(f"Batch size must be >= 1: {tok!r}")
        sizes.append(n)
    return sizes


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe Qwen3-TTS voice-clone batch sizes to find the OOM threshold.",
    )
    parser.add_argument(
        "--sizes",
        type=str,
        default=None,
        help=f"Comma/space separated batch sizes to test (default: {DEFAULT_SIZES})",
    )
    parser.add_argument(
        "--ref-audio",
        type=str,
        default=str(VOICES_DIR / "teacher.wav"),
        help="Reference WAV for voice clone (default: voices/teacher.wav)",
    )
    parser.add_argument(
        "--ref-text",
        type=str,
        default=None,
        help="Reference transcript; if omitted, read 'dialogue' from the matching .txt",
    )
    parser.add_argument(
        "--language",
        choices=sorted(LANGUAGE_MAP.keys()),
        default="zh",
        help="Language for all lines (default: zh)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Per-request HTTP timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--no-warmup",
        action="store_true",
        help="Skip the single-line warmup call that loads the model",
    )
    parser.add_argument(
        "--restart-between",
        action="store_true",
        help="Restart TTS before every size for a clean memory state",
    )
    args = parser.parse_args()

    ref_audio = Path(args.ref_audio)
    if not ref_audio.exists():
        print(f"ERROR: reference audio not found: {ref_audio}")
        return 1
    ref_audio_b64 = base64.b64encode(ref_audio.read_bytes()).decode("utf-8")

    if args.ref_text:
        ref_text = args.ref_text
    else:
        try:
            ref_text = _parse_dialogue(ref_audio.with_suffix(".txt"))
        except (ValueError, FileNotFoundError) as e:
            print(f"ERROR: {e}")
            print("Pass --ref-text explicitly, or provide a matching .txt with a dialogue field.")
            return 1

    texts_pool = _collect_dialogue_texts()
    if texts_pool:
        print(f"Using {len(texts_pool)} real dialogue lines from books_src/podcasts as the text pool.")
    else:
        print("No podcast dialogue lines found; using built-in sample lines.")
        texts_pool = _FALLBACK_TEXTS

    language = LANGUAGE_MAP[args.language]
    sizes = _parse_sizes(args.sizes)

    print()
    print(f"Reference audio: {ref_audio}")
    print(f"Reference text:  {len(ref_text)} chars")
    print(f"Language:        {language}")
    print(f"Batch sizes:     {sizes}")
    print(f"Timeout:         {args.timeout}s")
    print()

    svc = ServiceManager()
    was_running = svc.get_status("tts") == ServiceStatus.ONLINE

    results = []
    try:
        _ensure_tts(svc, force_restart=False)

        if not args.no_warmup and 1 not in sizes:
            print("[warmup] 1-line call to load the model…", flush=True)
            _probe(
                requests.Session(),
                _build_batch(texts_pool, 1),
                [language],
                ref_audio_b64,
                ref_text,
                args.timeout,
            )
            print("[warmup] done.", flush=True)
            print()

        session = requests.Session()
        for n in sizes:
            if args.restart_between and n != sizes[0]:
                _ensure_tts(svc, force_restart=True)

            if not _health():
                print(f"[size {n}] service unhealthy; restarting…", flush=True)
                try:
                    _ensure_tts(svc, force_restart=True)
                except Exception as e:
                    print(f"[size {n}] could not restart service: {e}")
                    break

            texts = _build_batch(texts_pool, n)
            languages = [language] * n
            result = _probe(session, texts, languages, ref_audio_b64, ref_text, args.timeout)
            result["size"] = n
            results.append(result)

            if result["status"] == "ok":
                print(
                    f"[size {n:>3}] OK       {result['elapsed_ms']:>7}ms  "
                    f"{result['total_chars']:>5} chars total"
                )
            else:
                print(
                    f"[size {n:>3}] FAILED   {result['elapsed_ms']:>7}ms  "
                    f"{result['total_chars']:>5} chars total  ({result['status']}: {result['detail']})"
                )
                # Larger batches will only be worse, so stop the scan here.
                break
    finally:
        if not was_running:
            try:
                svc.stop("tts")
                print("\nStopped TTS service.")
            except Exception as e:
                print(f"\nNote: could not stop TTS service: {e}")
        else:
            print("\nLeaving TTS service running (was active before this script).")

    print()
    print("=" * 72)
    print("RESULTS")
    print("=" * 72)
    for r in results:
        line = f"  size={r['size']:>3}  chars={r['total_chars']:>5}  {r['status']}"
        if r["status"] != "ok":
            line += f"  ({r['detail']})"
        print(line)

    successes = [r for r in results if r["status"] == "ok"]
    failures = [r for r in results if r["status"] != "ok"]

    if successes:
        threshold = successes[-1]
        print()
        print(
            f"Practical max batch size: {threshold['size']} items "
            f"({threshold['total_chars']} total chars) — "
            f"largest batch that completed successfully."
        )
    else:
        print("\nNo batch size completed successfully.")

    if failures:
        print(
            f"First failure: {failures[0]['size']} items "
            f"({failures[0]['total_chars']} total chars) — {failures[0]['status']}"
        )

    return 0 if successes else 1


if __name__ == "__main__":
    sys.exit(main())
