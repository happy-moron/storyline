import os
import re

def split_text(input_file_path, output_dir, chunk_size: int = 2000):
    # Read the input file
    with open(input_file_path, 'r', encoding='utf-8') as infile:
        content = infile.read()

    # Split the content into paragraphs using regex to match paragraph boundaries
    paragraphs = re.split(r'\n\n+', content)

    # Create the output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    # Get the input filename without extension
    input_filename = os.path.splitext(os.path.basename(input_file_path))[0]

    # Initialize file counter and current file content
    file_counter = 1
    current_file_content = ""

    for paragraph in paragraphs:
        if len(current_file_content) + len(paragraph) + 2 > chunk_size:
            if current_file_content.strip():
                output_file_path = os.path.join(output_dir, f"{input_filename}_{file_counter}.txt")
                with open(output_file_path, 'w', encoding='utf-8') as outfile:
                    outfile.write(current_file_content.strip())
                file_counter += 1
            current_file_content = ""

        current_file_content += paragraph + "\n\n"

    # Write the last file content if it exists
    if current_file_content:
        output_file_path = os.path.join(output_dir, f"{input_filename}_{file_counter}.txt")
        with open(output_file_path, 'w', encoding='utf-8') as outfile:
            outfile.write(current_file_content.strip())

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description="Split a text file into smaller files at natural paragraph boundaries.")
    parser.add_argument("--input_file_path", type=str, help="Path to the input text file")
    parser.add_argument("--output_dir", type=str, help="Path to the output directory")

    args = parser.parse_args()
    split_text(args.input_file_path, args.output_dir)
