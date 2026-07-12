#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
from zsp_llm_client.prompt_runner import PromptRunner

from storyline.book.parse_pipe_format import parse_tokenized_file

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

def process_json_file(filepath, dictionary, dict_file_path, prompt_template_path, models,
                      punctuation_skip: list[str] | None = None):
    if punctuation_skip is None:
        punctuation_skip = [',', '.', '?', '!', '，', '。', '？', '！', '"', '“', '”', '、']
    print(f"Processing file: {os.path.basename(filepath)}")
    try:
        data = parse_tokenized_file(filepath)

        for sentence_data in data:
            for token in sentence_data['t']:
                simplified_word = token[0]
                pinyin_word = token[1]
                dict_key = create_dictionary_key(simplified_word, pinyin_word)

                if simplified_word.strip() and simplified_word != pinyin_word and simplified_word not in punctuation_skip and simplified_word not in dictionary:
                    print(f"  Word not in dictionary: {simplified_word} {pinyin_word}")
                    prompt_input = simplified_word
 
                    runner = PromptRunner()
                    new_entry_json_str = runner.run(
                        prompt_template_path,
                        prompt_input,
                        models=models
                    )

                    try:
                        new_entry = json.loads(new_entry_json_str)
                        if new_entry and simplified_word in new_entry:
                            dictionary[simplified_word] = new_entry[simplified_word]
                            print(f"    Added to dictionary: {simplified_word}")
                        else:
                            print(f"    Error: Invalid dictionary entry received for {simplified_word}")
                            print(new_entry_json_str)

                    except json.JSONDecodeError as e:
                        print(f"    Error decoding JSON response for {simplified_word}: {e}")
                    except FileNotFoundError:
                        print(f"    Error: Output file not found for {simplified_word}")
                    except Exception as e:
                        print(f"    Error processing dictionary entry for {simplified_word}: {e}")
                else:
                    if dict_key in dictionary:
                        pass #print(f"  Word already in dictionary: {simplified_word}")
                    elif simplified_word.strip() in [',', '.', '?', '!', '，', '。', '？', '！', '"', '“', '”', '、']:
                        pass #print(f"  Skipping punctuation: {simplified_word}")
                    elif not simplified_word.strip():
                        pass #print(f"  Skipping empty word")

    except Exception as e:
        print(f"Error processing file {os.path.basename(filepath)}: {e}")
    finally:
        save_dictionary(dictionary, dict_file_path)
        print(f"Dictionary saved to: {dict_file_path}")

def process_json_files(input_dir, dict_file_path):
    dictionary = load_dictionary(dict_file_path)
    prompt_template_path = "prompts/create_single_dictionary_entry.txt"
    models = ["gemini-2.0-flash-exp","gemini-1.5-flash-latest"]
    #models = ["llama3.1"]

    for filename in os.listdir(input_dir):
        if filename.endswith(".json"):
            filepath = os.path.join(input_dir, filename)
            process_json_file(filepath, dictionary, dict_file_path, prompt_template_path, models)

def main():
    parser = argparse.ArgumentParser(description="Create a custom dictionary from JSON files.")
    parser.add_argument("--input_dir", type=str, help="Directory containing JSON files.")
    parser.add_argument("--dict_file_path", type=str, help="Path to the dictionary JSON file.")
    args = parser.parse_args()

    process_json_files(args.input_dir, args.dict_file_path)

if __name__ == "__main__":
    main()
