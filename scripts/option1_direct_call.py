#!/usr/bin/env python3
"""
Option 1: Call the venv's binary directly (no activation needed).

This calls <venv>/bin/omnivoice-infer directly, since omnivoice-infer is
a console-script entry point installed inside that venv (not a .py file
you'd pass to python). The same pattern works for a plain script by
swapping the executable for <venv>/bin/python and prepending the script path.

Argument passthrough: any extra args given to *this* wrapper script are
forwarded verbatim to omnivoice-infer. E.g.:

    python option1_direct_call.py \
        --text "This is a test for text to speech." \
        --ref_audio ref.wav \
        --ref_text "Transcription of the reference audio." \
        --output hello.wav

You can also import run_omnivoice() and call it directly from other code.
"""

import subprocess
import sys
from pathlib import Path

# --- Configure these for your setup ---
VENV_DIR = Path("/mnt/mac/git/omnivoice/.venv")
BINARY = VENV_DIR / "bin" / "omnivoice-infer"
DEFAULT_MODEL = "k2-fsa/OmniVoice"


def run_omnivoice(passthrough_args, model=DEFAULT_MODEL, timeout=None):
    """
    Run omnivoice-infer inside its own venv.

    passthrough_args: list of strings, e.g. ["--text", "...", "--ref_audio", "ref.wav"]
    model: value for --model (kept as a named default so callers don't have
           to repeat it every time; omit if you'd rather always require it
           explicitly via passthrough_args)
    """
    if not BINARY.exists():
        raise FileNotFoundError(
            f"omnivoice-infer not found at {BINARY}. "
            "Check VENV_DIR / that the package is installed in that venv."
        )

    cmd = [str(BINARY), "--model", model, *passthrough_args]

    result = subprocess.run(
        cmd,
        # capture_output=True,
        # text=True,
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
    # Forward everything passed to this script straight through.
    # e.g. `python option1_direct_call.py --text "..." --ref_audio ref.wav --output hello.wav`
    extra_args = sys.argv[1:]

    if not extra_args:
        # Fallback demo args matching the example in the prompt
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
