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

## 1 - Set up a RealWorld test harness to make sure the pipeline works

There should be a RealWorld test harness that is set up which can run the entire pipeline - text processing, audio generation (including both sentence based generation and the repeating/joined together longer audio. )

* run a short single chapter (multi-block) book text through the entire pipeline
* Leave the artifacts existing for manual examination after the run is finished, but clean expected locations before the test run so that it's re-runnable
* configured to use a local llm for the text processing stages (default 'qwen36-35b-a3b-nothink')
* generate sentence level audio and joined together audio

## 2 - Replace the 'translate_and_simplify' flow

The existing flow should be replaced with a separate, skippable 'simplify' prompt which runs to simplify a chapter of text to a targeted reading level and to format it into reading lines. 

The earlier translate_and_simplify flow was a failed experiment at joining the two stages.

Some handwritten source texts will already be suitably formatted and at an appropriate reading level; their chapters won't need to run through this stage.

## 3 - Add a chunking step to the pipeline

The pipeline should add a stage to break up the text into natural-reading chunks. The output of this should not change the content of the reading lines but should use a simple format to mark reading sections and their reading instructions. One possible approach is inserting custom, simple-to-parse tags that clearly mark sections with reading instructions (e.g. inserting <instruct text=" ">).

* Add the prompt
* Design the output format
    * rather than repeat full chapter, possibly just output previous/next sentences for tag insertion points. 
* Design output processing
    * Should validate that insertion points exist and are valid
    * extract out reading instructions and break the chunking source text into chunks (maybe just mapping reading instructions to start indices)

## 4 - Update audio generation to use chunks

* pass text in chunks (not reading lines) to tts audio gen 
* Pass reading instructions as 'instruct' for customvoice
* Pass resulting audio through forcedaligner to generate word timestamps
* Calculate reading line timestamps based on word timestamps
    * "split in the middle of the gap" between end of last word and start of next sentence.
* Design the output file format for chunk-based audio for the reader app to consume
* The aggregate/joined audio should use reading-line timestamps to grab audio segements/slices for joining


## 5 - Update the reader to use reading line timestamps for audio playback

* Update the reader to honor the chapter/chunk/reading line model
* implement reading-line playback based of timestamped section of audio according to reading line timestamp metadata
* 