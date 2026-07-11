#!/usr/bin/env python3
import os
import json
import argparse

def remove_underscores_recursively(data):
    if isinstance(data, str):
        return data.replace("_", " ")
    elif isinstance(data, list):
        return [remove_underscores_recursively(item) for item in data]
    elif isinstance(data, dict):
        return {key: remove_underscores_recursively(value) for key, value in data.items()}
    return data

def process_json_files(input_dir):
    for filename in os.listdir(input_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(input_dir, filename)
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                modified_data = remove_underscores_recursively(data)

                with open(filepath, 'w', encoding='utf-8') as f:
                    json.dump(modified_data, f, ensure_ascii=False, indent=2)
                print(f"Processed: {filename}")

            except Exception as e:
                print(f"Error processing {filename}: {e}")

def main():
    parser = argparse.ArgumentParser(description="Remove underscores from strings in JSON files in a directory.")
    parser.add_argument("--input_dir", type=str, help="Directory containing JSON files to process.")
    args = parser.parse_args()

    process_json_files(args.input_dir)

if __name__ == "__main__":
    main()
