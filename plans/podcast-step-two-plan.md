This step of the plan involves generating the e-reader files for a podcast for the index.html reader.

It has multiple phases.

# Phase 1 - write a script parser

The script parser should stand as an independent class/module which can validate a script for correctness. It doesn't depend on any part of the pipeline, just the script format

## Background Reading

prompts/podcast-script.md - this provides the formatting requirements for the script.
books_src/podcasts/talking-about-last-nights-soccer-results.txt - sample script 

## Requirements

Detailed requirements are laid out in the prompts/podcast-script which has the script format defined.
Basically we want to check/validate that the LLM output matches the prompt requirements as described.

* Parse a provided script text
  * Should parse each section and validate for well-formedness / correctness
    * all sections exist
    * sections have properly formatted 
    * mandatory keys exist for voice profiles
    * speaker ids exist in voice profiles for dialogue speakers
    * Intro, breakdown, outro sections are well formed with only teacher and student entries as expected.

# Phase 2 - Implement the podcast generation (text only) based on existing pipeline design

The goal of this is to create a design doc of how the podcast pipeline differs from the existing create_book pipeline and to write a design in plans/podcast-ereader-design.md

Most of the code to do the generation already exists, it's just a question of extracting out the dialogue and voice profiles from the script and handling any formatting differences, as well as dealing with any custom chunking and audio gen to use the voices from profiles.

## Background reading:

prompts/podcast-script.md - this provides the formatting requirements for the script.
books_src/podcasts/talking-about-last-nights-soccer-results.txt - sample script 
index.html (e-reader code)
books/manifest.json
src/storyline/create_book.py
src/storyline/parse_chunk_format.py
src/storyline/parse_pipe_format.py
src/storyline/tokenization_repair.py
src/storyline/update_manifest.py
src/storyline/create_custom_dict.py
src/storyline/get_json_from_prompt.py
src/storyline/check_tokenization.py

## Requirements

For this phase to be done, the podcast pipeline should create all of the files (minus the wav audio files) required for the reader to show the dialogue section of the podcast script.

* It must tokenize the dialogue lines
* It must create chunks and file artifacts based on the rule "One dialogue line == one chunk"
* It must create dictionary entries for new words (same as the existing pipeline)
* It must update the manifest when the text for podcast episode is created
* It should use the author 'podcasts' and the title is the normalized episode name. Only one chapter for each episode.

# Phase 3 - Implement the audio generation for podcasts for the e-book reader

The only part that's going to have audio generated is the "Dialogue" section of the script. However, the process for generating audio is different than for the standard pipeline.

In this pipeline, audio is always going to be generated from:

* Voice Design - reference text/audio created from the Voice Profiles "dialogue" property and voice profile as an 'instruct' to Qwen3's voice design
* Voice Clone - based on the created voice design for each speaker

The existing audio settings really don't apply for the podcast audio gen. 
The chunking can also be dramatically simpler as the dialogue lines are known and a simple rule of "one dialogue line == one chunk" can be applied.

So the work is to implement this to generate the expected sentence audio files for the e-reader.

## Out of scope

The main mp3 for the combined/extended podcast is out of scope, that will be done in "Part 3" of the master plan.

## Background reading

external_docs/qwen3-tts-flask
prompts/podcast-script.md - this provides the formatting requirements for the script.
books_src/podcasts/talking-about-last-nights-soccer-results.txt - sample script 
scripts/voice_design.py - existing sample code for parsing voice profiles and generating reference samples
src/storyline/audio/*
src/storyline/config/audio.toml - config for existing pipeline audio
index.html (e-reader code)
src/storyline/create_book.py
