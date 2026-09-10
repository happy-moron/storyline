# Backchaining support

# Implementation notes

The Qwen3 Forced Aligner model which is already in use by the book pipeline can produce word-timing boundaries for each word (start/end) in an audio sentence.

The lines of text in a podcast dialogue can be sent to this model and given the timing boundaries it should be a simple matter of calculating the offsets to replay the last section(s) of the existing audio to implement back-chaining playback.

It should be straightforward to add the word-timing metadata and the backchaining breakdown alongside existing artifacts without too much additional code work.

An additional prompt (prompts/back-chaining.md) should be able to generate all the back-chain breakdowns in one shot, but it may need a retry loop where it reruns/fixes any invalid backchains.

## Extensions to new generation 

The existing ereader section of the podcast pipeline should be updated with the following:

* Generation of backchaining breakdowns for the dialogue lines
  * see prompts/back-chaining.md
  * There will need to be a parser/validator which validates:
    * all sentences show up in the output
    * the final lines match the originals exactly for each line to back-chain
    * each stage of the back-chaining is an "endswith" match for the next.
* Generation of word-boundary metadata via Qwen3TTS Forced Aligner as part of the audio gen stage
 * this should happen for all the dialogue lines after the audio gen so that if qwen3tts is being used the server will still be up, but if omnivoice it will still be batched.

## Back-filling existing projects

There should be a script written for running against an existing podcast which would generate the backchain breakdowns (LLM) and word-boundary metadata (Qwen3 ASR forced aligner) for existing podcast episodes.

This would allow them to be updated to support the back-chaining playback.

# UI Changes

The ereader (index.html) should include support for playing the backchained audio. A button at the start of Chinese sentence which would play the entire back-chaining (honoring playback speed settings woul dbe a reasonable way of implementing this.)