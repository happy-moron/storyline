#!/usr/bin/env python3
"""
Option 2: Replicate `source venv/bin/activate` by manipulating the child
process's environment, then run the command by name (relying on the
modified PATH to find it) instead of an absolute path.

Useful when the target command (or something it spawns) depends on
activation side-effects, e.g. reading $VIRTUAL_ENV, or on further
subprocesses picking up the venv via $PATH.

Argument passthrough works the same way as option 1: anything passed to
this wrapper is forwarded verbatim to omnivoice-infer.

    python option2_env_activate.py \
        --text "This is a test for text to speech." \
        --ref_audio ref.wav \
        --ref_text "Transcription of the reference audio." \
        --output hello.wav
"""

import os
import subprocess
import sys
from pathlib import Path

# --- Configure this for your setup ---
VENV_DIR = Path("/path/to/omnivoice/venv")
DEFAULT_MODEL = "k2-fsa/OmniVoice"


def build_activated_env(venv_dir: Path) -> dict:
    """Return a copy of os.environ patched the way `activate` would patch it."""
    if not venv_dir.exists():
        raise FileNotFoundError(f"venv not found at {venv_dir}")

    env = os.environ.copy()
    env["VIRTUAL_ENV"] = str(venv_dir)
    env["PATH"] = f"{venv_dir / 'bin'}{os.pathsep}{env.get('PATH', '')}"
    # activate scripts unset PYTHONHOME; do the same to avoid interpreter
    # confusion if it happens to be set in the parent environment
    env.pop("PYTHONHOME", None)
    return env


def run_omnivoice(passthrough_args, model=DEFAULT_MODEL, timeout=None):
    env = build_activated_env(VENV_DIR)

    # Note: "omnivoice-infer" resolved by name via the patched PATH above,
    # not an absolute path — that's the whole point of this approach.
    cmd = ["omnivoice-infer", "--model", model, *passthrough_args]

    result = subprocess.run(
        cmd,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"omnivoice-infer failed (exit {result.returncode}):\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )

    return result.stdout


if __name__ == "__main__":
    extra_args = sys.argv[1:]

    if not extra_args:
        extra_args = [
            "--text", "This is a test for text to speech.",
            "--ref_audio", "ref.wav",
            "--ref_text", "Transcription of the reference audio.",
            "--output", "hello.wav",
        ]
        print("No CLI args given, using demo args:", extra_args)

    try:
        stdout = run_omnivoice(extra_args)
        print("Success. stdout:")
        print(stdout)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
