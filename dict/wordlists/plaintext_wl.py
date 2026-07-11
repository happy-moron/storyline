import json

# Assuming the JSON data is stored in a variable called 'data'
# or loaded from a file. Here's how to process it:

def print_dictionary_entries(json_data):
    for entry in json_data:
        for chinese_word, details in entry.items():
            pinyin = details["pinyin"]
            print(f"{chinese_word}\n{pinyin}")
            
            for definition in details["definition"]:
                print(definition["meaning"])
                example = definition["example"]
                print(f"{example['chinese']}\n{example['pinyin']}\n{example['english']}\n")


if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Split a text file into smaller files at natural paragraph boundaries.")
    parser.add_argument("--input", type=str, help="Path to the input text file")

    args = parser.parse_args()
    with open(args.input, 'r', encoding='utf-8') as f:
        data = json.load(f)
        print_dictionary_entries(data)
