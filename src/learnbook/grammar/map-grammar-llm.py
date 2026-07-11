import json
import re
import argparse
import os
from src.run_prompt import run_prompt_direct  # Assuming run_prompt.py is in the same directory
from collections import defaultdict

def map_input_dir(file_prefix, input_dir, output_dir, grammar_dir):
    
    # Ensure the output directory exists
    os.makedirs(output_dir, exist_ok=True)
    # Load grammar points from HSK files
    hsk_files = ['hsk1-grammar-index.json', 'hsk2-grammar-index.json', 'hsk3-grammar-index.json']
    grammar_points = load_grammar_points(hsk_files, grammar_dir)
    
        # Iterate over files in the input directory
    for filename in sorted(os.listdir(input_dir)):
        if filename.startswith(file_prefix):
            filepath = os.path.join(input_dir, filename)
            outfilepath = os.path.join(output_dir, filename)
            with open(filepath, "r", encoding="utf-8") as file:
                sentences = json.load(file)
   
            # Process sentences and get matches
            result = process_sentences(sentences, grammar_points)
    
            with open(outfilepath, "w",encoding="utf-8") as outfile:
                outfile.write(json.dumps(result, ensure_ascii=False, indent=2))

def load_grammar_points(hsk_files, grammar_dir):
    grammar_points = []
    for hsk_file in hsk_files:
        with open(os.path.join(grammar_dir,hsk_file), 'r', encoding='utf-8') as f:
            data = json.load(f)
            for category in data:
                for g in category['g']:
                    key, description, pattern = g
                    chinese_chars = set(re.findall(r'[\u4e00-\u9fff]', pattern))
                    grammar_points.append((key, chinese_chars))
    return grammar_points

def process_sentences(sentences, grammar_points):
    output = []
    for idx, sentence in enumerate(sentences):
        sentence_text = sentence['chinese']
        sentence_chars = set(re.findall(r'[\u4e00-\u9fff]', sentence_text))
        matched_grammar = []
        for key, chars in grammar_points:
            if chars & sentence_chars:
                matched_grammar.append(key)
        if matched_grammar:
            output.append({'i': int(idx), 'g': matched_grammar})
    return output

# Example usage (replace with actual file paths as needed)
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Process grammar patterns and sentences.')
    parser.add_argument('--output_dir', type=str, help='Directory to save the output JSON file')
    parser.add_argument('--input_dir', default='tests', type=str, help='Directory containing the sentence files')
    parser.add_argument('--grammar_dir', default='dict', type=str, help='Directory containing the grammar files and sentences.json')
    parser.add_argument("--file_prefix", type=str, required=True, help="Prefix of the input text files (e.g., 'chapter_').")

    args = parser.parse_args()

    map_input_dir(file_prefix=args.file_prefix, input_dir=args.input_dir, output_dir=args.output_dir, grammar_dir=args.grammar_dir)
    
