import json
import os
import argparse

from learnbook.book.parse_pipe_format import parse_tokenized_file

def load_dictionary(dict_file):
    """Loads the dictionary from a JSON file."""
    with open(dict_file, 'r', encoding='utf-8') as f:
        return json.load(f)

def create_dictionary_lookup(dictionary):
    """Creates a lookup dictionary for faster access."""
    lookup = {}
    for length, entries in dictionary.items():
        for entry in entries:
            simplified, pinyin, _, pos, _, _, _ = entry
            key = f"{simplified}_{pinyin.replace(' ', '')}"
            if simplified not in lookup:
                lookup[simplified] = {}
            if pinyin not in lookup[simplified]:
                lookup[simplified][pinyin] = []
            lookup[simplified][pinyin].append((key, pos))
    return lookup

def is_chinese(text):
    """Checks if a string is composed of Chinese characters."""
    for char in text:
        if not ('\u4e00' <= char <= '\u9fff'):
            return False
    return True

def process_sentence(sentence, dictionary_lookup, dictionary):
    """Processes a single sentence and adds dictionary keys."""
    not_found_words = set()
    for token in sentence['t']:
        text, pinyin, pos_list = token[:3]
        if len(token) < 4:
            token.append("")
        if text in dictionary_lookup and pinyin in dictionary_lookup[text]:
            matches = dictionary_lookup[text][pinyin]
            best_match = None
            for key, pos_str in matches:
                valid_pos = pos_list.split(',')
                dict_pos = pos_str.split(',')
                if any(p in valid_pos for p in dict_pos):
                    if best_match is None:
                        best_match = key
                    else:
                        # Find the entry with the highest frequency
                        current_freq = 0
                        best_freq = 0
                        for entry_list in dictionary.values():
                            for entry in entry_list:
                                if entry[0] == text and entry[1] == pinyin:
                                    if key == f"{entry[0]}_{entry[1].replace(' ', '_')}":
                                        current_freq = entry[2]
                                    if best_match == f"{entry[0]}_{entry[1].replace(' ', '_')}":
                                        best_freq = entry[2]

                        if current_freq > best_freq:
                            best_match = key
            if best_match:
                token.append(best_match)
            else:
                if is_chinese(text):
                    not_found_words.add(text)
                token.append("")
        else:
            if is_chinese(text):
                not_found_words.add(text)
            token.append("")
    return sentence, not_found_words

def process_files(source_dir, output_dir, file_prefix, dict_file):
    """Processes all text files in the source directory."""
    dictionary = load_dictionary(dict_file)
    dictionary_lookup = create_dictionary_lookup(dictionary)

    for filename in os.listdir(source_dir):
        all_not_found_words = set()
        if filename.startswith(file_prefix) and filename.endswith('.txt'):
            filepath = os.path.join(source_dir, filename)
            data = parse_tokenized_file(filepath)

            annotated_data = [process_sentence(sentence, dictionary_lookup, dictionary) for sentence in data]

            for sentence_data, not_found_in_sentence in annotated_data:
                all_not_found_words.update(not_found_in_sentence)
                annotated_sentences = [item[0] for item in annotated_data]  # Extract sentences, ignore not_found words

            output_filename = filename.replace('.txt', '_annotated.json')
            output_filepath = os.path.join(output_dir, output_filename)
            with open(output_filepath, 'w', encoding='utf-8') as f:
                json.dump(annotated_sentences, f, ensure_ascii=False, separators=(',', ':'))

        if args.not_found_output: # only write not_found_words file if path is provided
            not_found_filepath = os.path.join(args.not_found_output)
            with open(not_found_filepath, 'w', encoding='utf-8') as f:
                for word in sorted(list(all_not_found_words)): # sort for consistent output
                    f.write(word + '\n')

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Annotate Chinese sentences with dictionary keys.')
    parser.add_argument('--source_dir', default='books/wodehouse/json/tokenized', help='Source directory of JSON files.')
    parser.add_argument('--output_dir', default='books/wodehouse/json/annotated', help='Output directory of JSON files.')
    parser.add_argument('--file_prefix', default='', help='Prefix of JSON filenames.')
    parser.add_argument('--dict_file', default='dict/hsk.json', help='Path to the dictionary JSON file.')
    parser.add_argument('--not_found_output', default='dict/unfound.txt', help='Output file for not-found words.') # new argument
    args = parser.parse_args()
    process_files(args.source_dir, args.output_dir, args.file_prefix, args.dict_file)
