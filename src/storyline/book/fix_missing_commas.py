import json
import os
import re
from pathlib import Path


def fix_missing_commas(json_str: str) -> str:
    """Insert missing commas between adjacent JSON arrays.

    The pattern looks for ``["...", "...", "..."]`` followed directly by another
    ``[`` and inserts a comma and space.
    """
    pattern = re.compile(r'(\["[^\"]*",\s*"[^\"]*",\s*"[^\"]*"\])\s*(?=\[)')

    def replace_missing(match):
        return match.group(1) + ", "

    return pattern.sub(replace_missing, json_str)


def process_file(filepath: Path) -> bool:
    """Read *filepath*, attempt to fix missing commas and rewrite the file.

    Returns ``True`` if the file was modified, ``False`` otherwise.
    """
    try:
        filepath_str = str(filepath)
        with open(filepath_str, "r", encoding="utf-8") as f:
            content = f.read()

        # Validate JSON – if it loads we are done.
        try:
            json.loads(content)
            print(f"✓ {filepath_str} is already valid JSON")
            return False
        except json.JSONDecodeError:
            print(f"! {filepath_str} has JSON issues – attempting to fix")

        fixed_content = fix_missing_commas(content)
        # Verify the fix.
        json.loads(fixed_content)
        print(f"✓ Fixed {filepath_str}")

        backup_path = filepath_str + ".bak"
        if not os.path.exists(backup_path):
            os.rename(filepath_str, backup_path)
        with open(filepath_str, "w", encoding="utf-8") as f:
            f.write(fixed_content)
        return True
    except Exception as e:  # pragma: no cover – defensive
        print(f"Error processing {filepath_str}: {e}")
        return False


def process_directory(directory: str) -> None:
    """Process every ``*.json`` file in *directory*.

    This helper mirrors the original script's CLI behaviour.
    """
    directory_path = Path(directory)
    if not directory_path.is_dir():
        print(f"Error: {directory} is not a valid directory")
        return

    json_files = list(directory_path.glob('*.json'))
    if not json_files:
        print(f"No JSON files found in {directory}")
        return

    fixed = 0
    for json_file in json_files:
        if process_file(json_file):
            fixed += 1
    print(f"\nProcessing complete. Fixed {fixed} out of {len(json_files)} files.")
