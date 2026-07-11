#!/usr/bin/env python3
"""
Manual testing script for the book creation pipeline.
Tests each step individually on books_src/childrens/gossie.txt
"""

import sys
import os
import shutil
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent / "src"))

from storyline.book.split_text import split_text
from storyline.prompt_utils.run_prompt import run_prompt
from storyline.book.create_custom_dict import load_dictionary, process_json_file
from storyline.book.parse_pipe_format import parse_source_file, parse_tokenized_file

# Configuration
INPUT_TEXT = "books_src/childrens/gossie.txt"
OUTPUT_DIR = "books/childrens/gossie_test"
MAX_CHUNKS = 5

def setup_directories():
    """Create output directories"""
    Path(OUTPUT_DIR).mkdir(parents=True, exist_ok=True)
    Path(f"{OUTPUT_DIR}/split/source").mkdir(parents=True, exist_ok=True)
    Path(f"{OUTPUT_DIR}/split/simple").mkdir(parents=True, exist_ok=True)
    Path(f"{OUTPUT_DIR}/pipe/source").mkdir(parents=True, exist_ok=True)
    Path(f"{OUTPUT_DIR}/pipe/tokenized").mkdir(parents=True, exist_ok=True)

def test_step_1_split():
    """Step 1: Split text into chunks"""
    print("\n" + "="*60)
    print("STEP 1: Split text")
    print("="*60)
    
    split_text(INPUT_TEXT, f"{OUTPUT_DIR}/split/source")
    
    files = sorted(Path(f"{OUTPUT_DIR}/split/source").glob("*.txt"))
    print(f"Created {len(files)} chunk(s):")
    for f in files:
        size = f.stat().st_size
        print(f"  - {f.name} ({size} bytes)")
        with open(f) as fp:
            print(f"    Content:\n{fp.read()[:200]}...")

def test_step_2_simplify():
    """Step 2: Simplify text (no-op — simplification is now part of translate_and_simplify.md)"""
    print("\n" + "="*60)
    print("STEP 2: Simplify text (SKIPPED — built into translation prompt)")
    print("="*60)
    print("Simplify is now handled by translate_and_simplify.md.")

def test_step_3_translate():
    """Step 3: Translate simplified text"""
    print("\n" + "="*60)
    print("STEP 3: Translate text")
    print("="*60)
    
    files = sorted(Path(f"{OUTPUT_DIR}/split/source").glob("*.txt"))
    models = ["gemini-2.0-flash"]
    
    for i, source_file in enumerate(files[:MAX_CHUNKS]):
        source_stem = source_file.stem
        output_file = Path(f"{OUTPUT_DIR}/pipe/source/{source_stem}.txt")
        
        if output_file.exists():
            print(f"  Skipping {source_stem} (already translated)")
            continue
        
        print(f"  Translating {source_stem}...")
        try:
            run_prompt("prompts/translate_and_simplify.md", str(source_file), str(output_file), models=models)
            print(f"  ✓ Translated to {output_file}")
            
            # Show a snippet of the result
            with open(output_file) as fp:
                content = fp.read()
                print(f"    First 300 chars: {content[:300]}...")
        except Exception as e:
            print(f"  ✗ Error translating {source_stem}: {e}")

def test_step_4_tokenize():
    """Step 4: POS annotate/tokenize"""
    print("\n" + "="*60)
    print("STEP 4: POS annotate/tokenize")
    print("="*60)
    
    files = sorted(Path(f"{OUTPUT_DIR}/pipe/source").glob("*.txt"))
    models = ["gemini-2.0-flash"]
    
    for i, json_file in enumerate(files[:MAX_CHUNKS]):
        json_stem = json_file.stem
        output_file = Path(f"{OUTPUT_DIR}/pipe/tokenized/{json_stem}.txt")
        
        if output_file.exists():
            print(f"  Skipping {json_stem} (already tokenized)")
            continue
        
        print(f"  Tokenizing {json_stem}...")
        try:
            run_prompt("prompts/translate.txt", str(json_file), str(output_file), models=models)
            print(f"  ✓ Tokenized to {output_file}")
            
            # Show a snippet of the result
            with open(output_file) as fp:
                content = fp.read()
                print(f"    First 300 chars: {content[:300]}...")
        except Exception as e:
            print(f"  ✗ Error tokenizing {json_stem}: {e}")

def test_step_5_fix_commas():
    """Step 5: Fix missing commas (no-op for pipe format — no JSON to fix)"""
    print("\n" + "="*60)
    print("STEP 5: Fix missing commas (SKIPPED — not needed for pipe format)")
    print("="*60)
    print("Pipe format doesn't have JSON commas to fix.")

def test_step_6_dictionary():
    """Step 6: Update dictionary"""
    print("\n" + "="*60)
    print("STEP 6: Update dictionary")
    print("="*60)
    
    dictionary = load_dictionary("dict/custom_dict.json")
    files = sorted(Path(f"{OUTPUT_DIR}/pipe/tokenized").glob("*.txt"))
    
    for json_file in files[:MAX_CHUNKS]:
        json_stem = json_file.stem
        print(f"  Processing {json_stem}...")
        try:
            process_json_file(json_file, dictionary, "dict/custom_dict.json", "prompts/create_single_dictionary_entry.txt", models=["gemini-2.0-flash"])
            print(f"  ✓ Dictionary entry created for {json_stem}")
        except Exception as e:
            print(f"  ✗ Error updating dictionary: {e}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Test book creation pipeline")
    parser.add_argument('--simplify', action='store_true', help='Run simplify step')
    parser.add_argument('--skip-simplify', action='store_true', help='Skip simplify step')
    parser.add_argument('--skip-split', action='store_true', help='Skip split step')
    parser.add_argument('--skip-translate', action='store_true', help='Skip translate step')
    parser.add_argument('--skip-tokenize', action='store_true', help='Skip tokenize step')
    parser.add_argument('--skip-dict', action='store_true', help='Skip dictionary step')
    args = parser.parse_args()
    
    print(f"Testing pipeline on: {INPUT_TEXT}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Max chunks: {MAX_CHUNKS}")
    
    # Cleanup if output dir exists
    if Path(OUTPUT_DIR).exists():
        print(f"\nCleaning up existing output directory: {OUTPUT_DIR}")
        shutil.rmtree(OUTPUT_DIR)
    
    setup_directories()
    
    if not args.skip_split:
        test_step_1_split()
    
    if not args.skip_simplify:
        test_step_2_simplify()
    
    if not args.skip_translate:
        test_step_3_translate()
    
    if not args.skip_tokenize:
        test_step_4_tokenize()
        test_step_5_fix_commas()
    
    if not args.skip_dict:
        test_step_6_dictionary()
    
    print("\n" + "="*60)
    print("Pipeline test complete!")
    print("="*60)
    print(f"Output directory: {OUTPUT_DIR}")
    print("\nFiles created:")
    for root, dirs, files in os.walk(OUTPUT_DIR):
        for f in files:
            filepath = Path(root) / f
            print(f"  - {filepath.relative_to(OUTPUT_DIR)}")

if __name__ == '__main__':
    main()
