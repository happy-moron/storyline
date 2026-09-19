#!/usr/bin/env python3
"""
Generate flashcards from a word list file exported from the reader's saved
vocabulary feature.

Usage:
    python generate_flashcards_from_vocab_list.py <word_list.txt>

The word list file should contain one word per line (simplified Chinese).
Words are grouped into batches of 6; the last batch may be smaller.
Each batch produces:
  - A flashcard PDF under books/vocab/manual_N/cards.pdf
  - Image and text assets under books/vocab/manual_N/
  - Entries in books/flashcard-manifest.json (dictionary pane integration)

Partial batches (fewer than 6 words) are padded with empty placeholder
cards so the PDF still prints a full card sheet.  Only real words are
registered in the manifest.

The --start-batch option lets you resume from a specific batch number if
you need to re-run from a certain point.
"""

import argparse
import sys
from pathlib import Path

import storyline.logging
from storyline.config.pipeline_config import PipelineConfig
from storyline.flashcard import run_flashcard_pipeline_from_list
from storyline.logging import get_logger
from storyline.services.manager import ServiceManager


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Generate flashcards from a word list (one word per line). "
            "Words are grouped into batches of 6; each batch produces a PDF "
            "under books/vocab/manual_N/."
        ),
    )
    parser.add_argument(
        "word_list",
        type=Path,
        help="Path to a text file with one word per line",
    )
    parser.add_argument(
        "--vocab-base-dir",
        default="books/vocab",
        help="Base directory for output (default: books/vocab)",
    )
    parser.add_argument(
        "--prompt-template",
        default="prompts/flashcard-vocab-from-list.md",
        help="Path to the vocab extraction prompt template",
    )
    parser.add_argument(
        "-m", "--models",
        type=str,
        default=None,
        help="Comma-separated list of models (overrides config)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-extraction of vocab even if cache exists",
    )
    parser.add_argument(
        "--start-batch",
        type=int,
        default=None,
        help="Starting batch number (default: auto-detect from manifest)",
    )
    args = parser.parse_args()

    if not args.word_list.is_file():
        print(f"Error: file not found: {args.word_list}", file=sys.stderr)
        sys.exit(1)

    storyline.logging.init()
    log = get_logger("flashcard.vocab_list_cli")

    models = None
    if args.models:
        models = [m.strip() for m in args.models.split(",") if m.strip()]

    service_manager = ServiceManager()
    config = PipelineConfig.from_files_and_args(args)

    try:
        pdf_paths = run_flashcard_pipeline_from_list(
            word_list_path=args.word_list,
            vocab_base_dir=args.vocab_base_dir,
            prompt_template_path=args.prompt_template,
            service_manager=service_manager,
            models=models or config.models,
            force=args.force,
            start_batch=args.start_batch,
        )
        if pdf_paths:
            print(f"Generated {len(pdf_paths)} flashcard PDF(s):")
            for p in pdf_paths:
                print(f"  {p}")
        else:
            print("No PDFs generated.")
    finally:
        service_manager.stop("llm")
        service_manager.stop("image_gen")

    log.info("event=flashcard_vocab_list_cli_done")


if __name__ == "__main__":
    main()