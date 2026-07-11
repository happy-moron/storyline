import os
import fnmatch

def process_files(prefix: str, extension: str, directory: str, func):
    """
    Execute a function on every file in the specified directory 
    that matches the given prefix and extension.

    Args:
        prefix (str): The prefix to match against.
        extension (str): The file extension to match.
        directory (str): The directory to search for files.
        func (function): The function to execute on each matching file.
            This function should take one argument, the file path.

    Returns:
        None
    """

    # Build the full file name pattern
    pattern = f"{prefix}*.{extension}"

    # Iterate over all files in the directory
    for filename in os.listdir(directory):
        # Check if the file matches the pattern
        if fnmatch.fnmatch(filename, pattern):
            filepath = os.path.join(directory, filename)
            try:
                with open(filepath, 'r') as f:
                    # Read the file contents and pass to the function
                    func(f.read())
            except Exception as e:
                print(f"Error processing {filename}: {e}")

