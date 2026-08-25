# Overview

The podcast pipeline generates:

* podcast scripts based on HSK grammar points and a theme
  * scripts should be saved in books_src/podcasts/ as text files
* An e-reader text (with sentence audio) of the sample dialogue section of the podcast
  * saved in books/ and formatted for the same reader app format as e-books (index.html)
* An mp3 audio of the full podcast, expanded from the script
  * uses qwen3-tts "voice design" for the podcast hosts and dialogue characters

A lesson script is designed around around:

* one or more HSK grammar points
* a theme (e.g. "eating at a restaurant," "buying a car," "at the gym," "hanging in the living room," etc.)
* a set of theme-related vocabulary


# Script Format and structure

The podcast script is described in prompts/podcast-script.md

## Example of host text markup

zh=teacher
This is an introduction to the grammar point... blah blah blah
en=student
How does this work in context?

## Example of dialogue markup (Numeric speaker ids)

zh=1
我可以对你直言不讳吗？
Can I speak honestly with you?
zh=2
没关系，先生。
That is okay, sir

# Podcast Audio Format

The mp3 audio for the podcast expands/extends the base script with some repetition.
The audio should be stitched together for a single mp3 with the following content blocks:

* Intro
* Dialogue
  * the full dialogue sequence, repeated twice for a total of three times in the character voices
* Dialogue - Line By Line with English Translation
  * The teacher host reads a line in chinese, and the student host reads the English translation. This is done 3x for each line. 
* Breakdown of Dialogue
* Outro 

# Pipeline design

The pipeline has three parts. The first part generates the podcast scripts into books_src/podcasts
The second part generates the e-reader text and audio files for the dialogue section of the podcast.
The third part generates the full, expanded mp3 audio for the podcast.


## Part One - Generating scripts

The pipeline for generating these should:

* Prepare the next lesson topic
  * Load the list of HSK grammar points
    * dict/hsk[1-3]-grammar-index.json
  * Load the list of candidate lesson themes
    * src/storyline/podcast/topics.md
  * Load the map/registry of already covered topics
    * src/storyline/podcast/existing-episodes.csv
  * Take the next two _unused_ grammar points
    * Will have to go through the unique already covered grammar points in existing-episodes and remove them from the list
  * Take the next available theme/topic
* Record the decided theme/grammar points in the mapping csv
  * Use the next available episode number as the podcast id (based on what's in the existing episodes )
* LLM Prompt - brainstorm a set of candidate vocabulary words based on the theme
  * prompts/podcast-vocab.md
* LLM Prompt - Create the podcast script (prompts/podcast-script.md)
  * (needs to be templated!!)
  * Inputs are chosen grammar point(s), theme, and vocab
  * Output is the formatted, parseable script in books_src/podcasts

NOTES: use qwen38-27b for the model and the prompts. 

## Part Two - E-book reader for dialogue

 The "Dialogue" section of the script should be readable (with English translation and audio for each sentence) in the main index.html e-reader of this project. The pipeline needs to implement this.

* Parse the script
  * Should parse each section and validate for well-formedness / correctness
    * all sections exist
    * sections have properly formatted 
    * mandatory keys exist for voice profiles
    * speaker ids exist in voice profiles for dialogue speakers
    * Intro, breakdown, outro sections are well formed with only teacher and student entries as expected.
  * Voice Profiles
    * sample processing exists in scripts/voice_design.py and scripts/voice_design_batch.py
    * map the genders in voices/gender-keys.txt
    * map the keys in voices/mandarin-keys.txt
* Extract out the "Dialogue" and "Voice Profiles" section
* Voice Design based on voice profiles
  * Dialogue Profiles - use the voice_design feature of the Qwen3TTS to create reference samples for the dialogue speakers
    * these should be named after the theme (normalized / lowercase with underscores) and the speaker id (e.g. at_the_gym_1.txt) and saved in voices/
* Dialogue Preparation for e-reader (index.html)
  * Extract out the "Dialogue" section of the script
  * Format the text similar to the simplified format already used in the create_book pipeline
  * Generate tokenized text to get the pinyin and tokens to work with the reader
  * The dialogue will need special chunking where each speaker's line/block is a single chunk and reading line
* Audio generation for Dialogue Sentences 
  * The dialogue will need special audio gen that uses voice_clone for the reference samples and the "dialogue" keys from the voice profiles for the dialogue speakers.
* E-book generation
  *

## Part Three - Podcast MP3 Audio gen

* The pipeline should generate all the audio using the "Voice clone" feature 
  * hosts should use the pre-existing reference audio
      * voices/student.txt & voices/student.wav
      * voices/teacher.txt & voices/teacher.wav
  * Dialogue characters should use the generated voice samples (and reference text from the "dialogue" property in their voice files)

The MP3 audio should have expanded content from the base script, as follows. It should re-use the generated sentence audio from the e-reader in part two, if it exists. This can save it having to redo the voice design and audio generation for the dialogue portion.

* Intro
* Dialogue
  * the full dialogue sequence, repeated twice for a total of three times in the character voices
* Dialogue - Line By Line with English Translation
  * The teacher host reads a line in chinese, and the student host reads the English translation. This is done 3x for each line. 
* Breakdown of Dialogue
* Outro 

