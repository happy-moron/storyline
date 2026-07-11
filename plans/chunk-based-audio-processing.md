# Plan Summary (DO NOT MODIFY)

This plan implements "chunk-based" audio processing.
The current state (prior to plan implementation) of the create_book pipeline does sentence by sentence audio processing. Texts are split into blocks but the audio is rendered sentence by sentence to support the HTML/JS/CSS reader that plays one sentence at a time.

This has the limitation that isolated sentences don't sound nice thorough TTS because they lack the surrounding context (scene, etc.) which is implicit for "natural" reading

The goal of this plan is to move to processing text in natural reading blocks of text or "chunks" which have enough context for the TTS model to produce natural sounding speech.

This means that audio files will change from per-sentence audio files to longer audio files, which will then be run through a new ForcedAligner model to identify word timestamps. 

The text processing is based on having a sentence per each line. Sometimes the models mess up and put a couple short sentences on the same line; this doesn't really matter. By tracking the number of words per line in the source texts while assembling the reading chunks, the line boundaries can be clearly marked thoroughout the entire process.

So after running forced alignment, the pipeline will need to calculate the start/end line timestamps. The api supports start/end timestamp information, so processing probably wants to calculate "middle of the pause" based on the start of next word (if it exists).

The longer stitched audio file construction (e.g. with readers alternating) will need to rely on the known line wordcounts/boundaries to slice/cut audio sections to assemble the needed audio.

The HTML/JS/CSS reader needs to be updated to work on the new model. Language should be made consistent across the pipeline/reader:
* "Chapters" for the higher-level split blocks
* "Chunks" for the natural-reading chunks
* "Line" for each reading line

The reader app should still support playing audio per-line but it will need to use known line-wordcounts and word timestamps to play correct audio sections within a chunk.

# Background / Reading

The docs for the ForcedAligner endpoint are in
external_docs/qwen3-tts-flask/README.md

# Implementation Order / Plan

## 1 - 