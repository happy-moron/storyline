# Flashcard Pipeline design

The pipeline needs to extract out vocab words from a podcast script, generate images for the flashcards, and create a printable pdf from images and text entries.

Sample pieces for doing individual stages exist, but they need to be put together

@prompts/flashcard-vocab-from-script.md
@scripts/generate_hidream_image.py
@scripts/make_flashcards.py


## Get the vocab items from the script

Input - name of the podcast episode (script file)

- Start the LLM using service manager
- Use an LLM prompt to extract a set of six vocab words from the script
    - prompts/flashcard-vocab-from-script.md
    - write to a simple text file, one one each line - character(s), pinyin, english
    - output has the text content for the flashcard *and* the text prompt

- Parse/validate the output
    - number of entries
    - number of items in each entry

Just do 3x retries of the original prompt if there are errors.

Each entry has 6 lines of flashcard text content and a final line with the text prompt for generating the flashcard image.

Example entry:

手臂
shǒu bì
arm
我的手臂受伤了。
Wǒ de shǒu bì shòu shāng le.
My arm is injured.
A bare arm and shoulder

## Prep the flashcard input and generate

- Create a flashcards/ folder under the podcast episode in the books/ tree
    - save the flashcard text files into that folder (text_[1-6].txt)
- Start the ComfyUI service using service manager
    - generate images add a 'style suffix' to the text prompts: + ", coloring book, line drawing, simple, black and white"
 - generate the images (img_[1-6].png) within the flashcard/ directory for the episode
   - Timeout for this should be about 15min per image - the image gen is slow on this hardware
   - No retries for this
- Shutdown comfyUI once all the images are done.
- Generate the pdf for the files