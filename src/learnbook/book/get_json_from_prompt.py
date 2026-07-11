#!/usr/bin/env python3
import os
import sys
import argparse
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from learnbook.prompt_utils.run_prompt import run_prompt

def get_json_from_prompt(prompt, input_dir, input_ext, output_dir, output_ext, file_prefix, pre_process_module, models):
    models_list = [model.strip() for model in models.split(',')]

    file_index = 1
    while True:
        input_filename = f"{file_prefix}{file_index}.{input_ext}"
        input_filepath = os.path.join(input_dir, input_filename)

        if not os.path.exists(input_filepath):
            print(f"Could not find path {input_filepath}")
            break

        os.makedirs(output_dir, exist_ok=True)
        output_filename = f"{file_prefix}{file_index}.{output_ext}"
        output_filepath = os.path.join(output_dir, output_filename)

        if not os.path.exists(output_filepath):
            print(f"Running prompt for file {input_filename}")
            run_prompt.run_prompt(prompt, input_filepath, output_filepath, pre_process_module, models=models_list)

        file_index += 1

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run translation prompt on a series of files.")
    parser.add_argument("--prompt", type=str, required=True, help="Path to the prompt template file.")
    parser.add_argument("--input_dir", type=str, required=True, help="Input directory containing source files.")
    parser.add_argument("--input_ext", type=str, required=False, default="json", help="Extension of source files.")
    parser.add_argument("--output_ext", type=str, required=False, default="json", help="Extension of target files.")
    parser.add_argument("--output_dir", type=str, required=True, help="Output directory containing responses.")
    parser.add_argument("--file_prefix", type=str, required=True, help="Prefix of the input text files (e.g., 'chapter_').")
    parser.add_argument("--models", type=str, required=True, help="Comma-separated list of models to use (e.g., 'gemini-2.0-flash,gpt-4').")
    parser.add_argument("--pre_process_module", required=False, type=str, default=None)

    args = parser.parse_args()

    get_json_from_prompt(args.prompt, args.input_dir, args.input_ext, args.output_dir, args.output_ext, args.file_prefix, args.pre_process_module, args.models)
