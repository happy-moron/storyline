import json
import os
from collections import defaultdict

# Default paths
INPUT_DIR = "../complete-hsk-vocabulary/wordlists/exclusive/new/"
OUTPUT_PATH = "dict/hsk.json"

def flatten_entry(entry, hsk_level):
    """Flatten an entry and extract required fields."""
    simplified = entry["simplified"]
    pinyin = entry["forms"][0]["transcriptions"]["pinyin"].replace(" ", "_")
    frequency = entry["frequency"]
    pos = ",".join(entry["pos"])
    meanings = ",".join(entry["forms"][0]["meanings"])
    classifiers = ",".join(entry["forms"][0]["classifiers"])
    
    # Create the dictionary key
    key = f"{simplified}_{pinyin}"
    
    # Create the flattened entry
    flattened_entry = [
        simplified,
        pinyin.replace("_", " "),  # Revert pinyin to spaces for readability
        frequency,
        pos,
        meanings,
        classifiers,
        hsk_level
    ]
    
    return key, flattened_entry

def combine_files(input_dir, output_path):
    """Combine all JSON files in the input directory into a single dictionary."""
    combined_dict = defaultdict(list)
    
    # Iterate over files in the input directory
    for filename in sorted(os.listdir(input_dir)):
        if filename.endswith(".json") and not filename.endswith(".min.json"):
            hsk_level = int(filename.split(".")[0])  # Extract HSK level from filename
            filepath = os.path.join(input_dir, filename)
            
            with open(filepath, "r", encoding="utf-8") as file:
                data = json.load(file)
                for entry in data:
                    key, flattened_entry = flatten_entry(entry, hsk_level)
                    combined_dict[len(entry["simplified"])].append(flattened_entry)
    
    # Sort entries by frequency (descending) within each sub-array
    for length in combined_dict:
        combined_dict[length].sort(key=lambda x: x[2], reverse=True)
    
    # Convert defaultdict to a regular dict for JSON serialization
    combined_dict = dict(combined_dict)
    
    # Write the combined dictionary to the output file
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(combined_dict, file, ensure_ascii=False, indent=2)

if __name__ == "__main__":
    combine_files(INPUT_DIR, OUTPUT_PATH)
    print(f"Combined dictionary saved to {OUTPUT_PATH}")