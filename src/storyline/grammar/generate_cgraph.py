#!/usr/bin/env python3
import os
import json
import argparse
from pathlib import Path
from src.run_prompt import run_prompt_direct  # Assuming run_prompt.py is in the same directory

def create_dictionary_key(simplified, pinyin):
    return f"{simplified}_{pinyin.replace(' ', '_')}"

def load_dictionary(dict_file_path):
    if os.path.exists(dict_file_path):
        with open(dict_file_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    else:
        return {}

def save_dictionary(dictionary, dict_file_path):
    os.makedirs(os.path.dirname(dict_file_path), exist_ok=True)
    with open(dict_file_path, 'w', encoding='utf-8') as f:
        json.dump(dictionary, f, ensure_ascii=False, indent=2)

def process_json_files(input_dir, output_dir):
    prompt_template_path = "prompts/annotate_pos_deepseek_cgraph.txt"
    models = ["gemini-2.0-flash-exp","gemini-1.5-flash-latest"]
    #models = ["llama3.1"]

    for filename in os.listdir(input_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(input_dir, filename)
            print(f"Processing file: {filename}")
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                sentences = ""
                for sentence_data in data:                                      
                    sentences = sentences + sentence_data["chinese"] + "\n"

                cgraph_str = run_prompt_direct(
                    prompt_template_path,
                    sentences, 
                    None, # No pre-process module
                    models
                )

                outfilepath = os.path.join(output_dir, filename)
                with open(outfilepath, "w", encoding="utf-8") as outfile:
                    outfile.write(cgraph_str)

            except Exception as e:
                print(f"Error processing file {filename}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Generate Constituency graphs for sentences.")
    parser.add_argument("--input_dir", type=str, help="Directory containing JSON files.")
    parser.add_argument("--output_dir", type=str, help="Directory containing JSON files.")
    args = parser.parse_args()

    process_json_files(args.input_dir, args.output_dir)

if __name__ == "__main__":
    main()
