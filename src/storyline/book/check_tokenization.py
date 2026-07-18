import argparse
import os
import re

import storyline.logging
from storyline.logging import get_logger
from storyline.book.parse_pipe_format import parse_source_file, parse_tokenized_file
from storyline.book.tokenization_repair import validate_full, ErrorType

_log = get_logger("book.check_tokenization")

def parse_args():
    parser = argparse.ArgumentParser(description='Compare sentences between source and tokenized files.')
    parser.add_argument('source_dir', help='Path to the directory containing source JSON files')
    parser.add_argument('tokenized_dir', help='Path to the directory containing tokenized JSON files')
    parser.add_argument('prefix', help='Prefix for the files to compare (e.g., "file" for file1.json, file2.json)')
    return parser.parse_args()

def find_matching_files(source_dir, tokenized_dir, prefix):
    """Find pairs of files with the same prefix and number in both directories."""
    source_files = {}
    tokenized_files = {}
    pattern = re.compile(re.escape(prefix) + r'(\d+)\.txt$')

    for dir_path, file_dict in [(source_dir, source_files), (tokenized_dir, tokenized_files)]:
        for filename in os.listdir(dir_path):
            match = pattern.match(filename)
            if match:
                num = int(match.group(1))
                full_path = os.path.join(dir_path, filename)
                file_dict[num] = full_path

    common_nums = set(source_files.keys()).intersection(tokenized_files.keys())
    file_pairs = []
    for num in sorted(common_nums):
        file_pairs.append((source_files[num], tokenized_files[num]))
    return file_pairs

def compare_sentences(source_data, tokenized_data):
    """Compare sentences and return mismatches with sentence content."""
    mismatches = []
    min_len = min(len(source_data), len(tokenized_data))
    for i in range(min_len):
        source_sent = source_data[i].get('chinese', '')
        tokenized_tokens = tokenized_data[i].get('t', [])
        reconstructed = ''.join(token[0] for token in tokenized_tokens)
        source_no_ws="".join(source_sent.split())
        reconstructed_no_ws="".join(reconstructed.split())
        if source_no_ws != reconstructed_no_ws:
            mismatches.append((i, source_no_ws, reconstructed_no_ws))
    
    if len(source_data) != len(tokenized_data):
        _log.warning(f"File has different sentence counts: source={len(source_data)}, tokenized={len(tokenized_data)}")
    return mismatches

def main():
    args = parse_args()
    storyline.logging.init()

    file_pairs = find_matching_files(args.source_dir, args.tokenized_dir, args.prefix)
    if not file_pairs:
        _log.warning("No matching file pairs found.")
        return

    total_errors = 0
    for source_path, tokenized_path in file_pairs:
        try:
            source_data = parse_source_file(source_path)
        except Exception as e:
            _log.error(f"Failed to load {source_path}: {e}")
            continue

        raw_text = None
        try:
            with open(tokenized_path, "r", encoding="utf-8") as f:
                raw_text = f.read()
            tokenized_data = parse_tokenized_file(tokenized_path)
        except Exception as e:
            _log.error(f"Failed to load {tokenized_path}: {e}")
            continue

        # Strip whitespace from source Chinese for comparison
        source_sentences = ["".join(item["chinese"].split()) for item in source_data]
        raw_lines = raw_text.strip().split("\n") if raw_text else []

        filename = os.path.basename(source_path)
        report = validate_full(source_sentences, tokenized_data, raw_lines)

        if report.is_clean:
            _log.info(f"{filename}: OK ({len(source_sentences)} sentences)")
            continue

        for err in report.errors:
            _log.info(f"{filename}, sentence {err.sentence_index}: [{err.error_type.name}]")
            if err.source_text:
                _log.info(f"  Source:      {err.source_text}")
            if err.tokenized_line:
                _log.info(f"  Tokenized:   {err.tokenized_line}")
            _log.info(f"  {err.detail}")
            _log.info("")
            total_errors += 1

    if total_errors == 0:
        _log.info("All sentences validated successfully.")
    else:
        _log.info(f"Total errors found: {total_errors}")

if __name__ == '__main__':
    main()