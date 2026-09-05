#!/usr/bin/env python3
"""
Option 1 (batch): Call the venv's binary directly (no activation needed).

For large test lists the batch binary may hit GPU memory limits when processing
all entries at once.  This script splits the JSONL into chunks, runs
omnivoice-infer-batch once per chunk, and collects all results into a single
output directory.

    python option1_direct_call_batch.py \
        --test_list test_batch.jsonl \
        --res_dir results/

    python option1_direct_call_batch.py \
        --test_list test_batch.jsonl \
        --res_dir results/ \
        --batch_size 4

The script writes intermediate batch files into a temp directory that is
cleaned up on exit (unless --keep-temp is passed).
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

VENV_DIR = Path("/mnt/mac/git/omnivoice/.venv")
BINARY = VENV_DIR / "bin" / "omnivoice-infer-batch"
DEFAULT_MODEL = "k2-fsa/OmniVoice"
DEFAULT_BATCH_SIZE = 8


def run_omnivoice_batch(test_list, res_dir, model=DEFAULT_MODEL, timeout=None):
    if not BINARY.exists():
        raise FileNotFoundError(
            f"omnivoice-infer-batch not found at {BINARY}. "
            "Check VENV_DIR / that the package is installed in that venv."
        )

    cmd = [
        str(BINARY),
        "--model", model,
        "--test_list", str(test_list),
        "--res_dir", str(res_dir),
    ]

    result = subprocess.run(
        cmd,
        timeout=timeout,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"omnivoice-infer-batch failed (exit {result.returncode})"
        )


def run_omnivoice_batch_multi(
    test_list,
    res_dir,
    model=DEFAULT_MODEL,
    batch_size=DEFAULT_BATCH_SIZE,
    timeout=None,
    keep_temp=False,
):
    test_list = Path(test_list)
    res_dir = Path(res_dir)
    res_dir.mkdir(parents=True, exist_ok=True)

    entries = []
    with test_list.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))

    if not entries:
        print("Test list is empty. Nothing to do.")
        return

    n_batches = (len(entries) + batch_size - 1) // batch_size

    tmp = Path(tempfile.mkdtemp(prefix="omnivoice_batch_"))
    if keep_temp:
        print(f"Temp dir: {tmp}")
    try:
        for i in range(n_batches):
            start = i * batch_size
            end = min(start + batch_size, len(entries))
            batch_entries = entries[start:end]

            batch_file = tmp / f"batch_{i:04d}.jsonl"
            batch_res_dir = tmp / f"batch_{i:04d}_results"
            batch_res_dir.mkdir()

            with batch_file.open("w", encoding="utf-8") as f:
                for entry in batch_entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")

            print(
                f"[batch {i + 1}/{n_batches}] "
                f"entries {start + 1}-{end} of {len(entries)} "
                f"({len(batch_entries)} items)"
            )
            run_omnivoice_batch(batch_file, batch_res_dir, model=model, timeout=timeout)

            for src in batch_res_dir.iterdir():
                dst = res_dir / src.name
                if dst.exists():
                    dst.unlink()
                shutil.move(str(src), str(dst))

            batch_res_dir.rmdir()

        print(f"\nAll {n_batches} batches complete. Output in {res_dir}/")
    finally:
        if not keep_temp:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Batch TTS via omnivoice-infer-batch (with optional chunking)"
    )
    parser.add_argument(
        "--test_list", default="test_batch.jsonl",
        help="Path to JSONL test list",
    )
    parser.add_argument(
        "--res_dir", default="results/",
        help="Directory for output audio files",
    )
    parser.add_argument(
        "--model", default=DEFAULT_MODEL,
        help=f"Model name (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--batch_size", type=int, default=DEFAULT_BATCH_SIZE,
        help=f"Entries per sub-batch (default: {DEFAULT_BATCH_SIZE})",
    )
    parser.add_argument(
        "--timeout", type=int, default=None,
        help="Timeout in seconds per batch call",
    )
    parser.add_argument(
        "--keep-temp", action="store_true",
        help="Keep temporary batch JSONL files after completion",
    )
    args = parser.parse_args()

    try:
        run_omnivoice_batch_multi(
            test_list=args.test_list,
            res_dir=args.res_dir,
            model=args.model,
            batch_size=args.batch_size,
            timeout=args.timeout,
            keep_temp=args.keep_temp,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)